import Foundation

/// Bridges engine WebSocket events to SwiftUI state updates.
///
/// Converts raw EngineEvent payloads into:
/// - Agent timeline turns (added to the conversation)
/// - Task status updates (reflected in TaskBar)
/// - Permission approval requests (rendered inline in the conversation)
///
/// Data flow:
/// ```
/// EngineWebSocket.onEvent → EngineEventHandler.handle()
///   ├── task:message   → chatMessages.append(AgentMessage)
///   ├── task:progress  → taskProgress[taskId] = (step, total)
///   ├── task:completed → taskStore.updateStatus(.completed)
///   ├── task:failed    → taskStore.updateStatus(.failed) + error turn
///   └── task:cancelled → taskStore.updateStatus(.cancelled)
/// ```
@Observable
final class EngineEventHandler {

    // MARK: - Observable State

    /// New agent events to display as ordered timeline turns.
    private(set) var pendingMessages: [AgentChatMessage] = []

    /// Active task progress: taskId → (current step, total steps, summary)
    private(set) var taskProgress: [String: TaskProgressInfo] = [:]

    /// Permission requests stay in the timeline after a decision as audit records.
    private(set) var permissionRequests: [PermissionApproval] = []

    /// Monotonic signal used by snapshot consumers to reconcile after any engine event.
    private(set) var revision: UInt64 = 0

    var pendingApprovals: [PermissionApproval] {
        permissionRequests.filter { $0.isPending && $0.expiresAt > Date() }
    }

    /// Latest notification (for toast display). Settable so UI can dismiss.
    var latestNotification: AgentNotification?

    // MARK: - Dependencies

    var taskStore: TaskStore?
    var approvalSender: (any ApprovalDecisionSending)?

    // MARK: - Handle Events

    func handle(_ event: EngineEvent) {
        revision &+= 1
        switch event.type {
        case "task:message":
            handleTaskMessage(event.payload)

        case "task:progress":
            handleTaskProgress(event.payload)

        case "task:completed":
            handleTaskCompleted(event.payload)

        case "task:failed":
            handleTaskFailed(event.payload)

        case "task:cancelled":
            handleTaskCancelled(event.payload)

        case "task:dispatched":
            handleTaskDispatched(event.payload)

        case "task:created":
            // task already created locally, no-op
            break

        case "permission:request":
            handlePermissionRequest(event.payload)

        case "notification":
            handleNotification(event.payload)

        default:
            break
        }
    }

    // MARK: - Consume Messages

    /// Pop all pending messages for the ordered conversation timeline.
    func consumeMessages() -> [AgentChatMessage] {
        let msgs = pendingMessages
        pendingMessages.removeAll()
        return msgs
    }

    /// Rebuild pending approvals from the authoritative HTTP snapshot after reconnect.
    func reconcilePendingApprovals(_ snapshots: [EnginePermissionSnapshot]) {
        let resolvedAudit = permissionRequests.filter { !$0.isPending }
        permissionRequests = resolvedAudit + snapshots.map(\.approval)
    }

    /// Resolve a permission request and retain it as an in-place audit record.
    @discardableResult
    func resolveApproval(
        id: String,
        approved: Bool,
        rememberScope: Bool = false
    ) async -> Bool {
        guard permissionRequests.contains(where: { $0.id == id && $0.isPending }) else {
            return false
        }

        do {
            guard let approvalSender else {
                throw ApprovalDecisionError.engineUnavailable
            }
            try await approvalSender.sendApproval(
                approvalId: id,
                approved: approved,
                rememberScope: rememberScope
            )
            guard let index = permissionRequests.firstIndex(where: { $0.id == id }) else {
                return false
            }
            permissionRequests[index].decision = approved ? .approved : .denied
            permissionRequests[index].resolvedAt = Date()
            permissionRequests[index].resolutionError = nil
            return true
        } catch {
            guard let index = permissionRequests.firstIndex(where: { $0.id == id }) else {
                return false
            }
            permissionRequests[index].resolutionError = error.localizedDescription
            return false
        }
    }

    // MARK: - Internal Handlers

    private func handleTaskMessage(_ p: EngineEventPayload) {
        let msg = AgentChatMessage(
            taskId: p.taskId ?? "",
            agentId: p.authorId ?? "agent",
            type: AgentMessageType(rawValue: p.messageType ?? "text") ?? .text,
            content: p.content ?? "",
            tool: p.tool,
            toolOutput: p.output,
            seq: p.seq ?? 0,
            timestamp: Date()
        )
        pendingMessages.append(msg)
    }

    private func handleTaskProgress(_ p: EngineEventPayload) {
        guard let taskId = p.taskId else { return }
        taskProgress[taskId] = TaskProgressInfo(
            step: p.step ?? 0,
            total: p.total ?? 0,
            summary: p.summary ?? p.status ?? "running"
        )
    }

    private func handleTaskCompleted(_ p: EngineEventPayload) {
        guard let taskId = p.taskId else { return }
        taskProgress.removeValue(forKey: taskId)
        Task { await taskStore?.updateStatus(taskId: taskId, status: .completed) }

        // Add a compact completion event to the timeline.
        pendingMessages.append(AgentChatMessage(
            taskId: taskId,
            agentId: "system",
            type: .status,
            content: "Task completed",
            seq: 0,
            timestamp: Date()
        ))
    }

    private func handleTaskFailed(_ p: EngineEventPayload) {
        guard let taskId = p.taskId else { return }
        taskProgress.removeValue(forKey: taskId)
        Task { await taskStore?.updateStatus(taskId: taskId, status: .failed) }

        pendingMessages.append(AgentChatMessage(
            taskId: taskId,
            agentId: "system",
            type: .error,
            content: p.error ?? "Task failed",
            seq: 0,
            timestamp: Date()
        ))
    }

    private func handleTaskCancelled(_ p: EngineEventPayload) {
        guard let taskId = p.taskId else { return }
        taskProgress.removeValue(forKey: taskId)
        Task { await taskStore?.updateStatus(taskId: taskId, status: .cancelled) }
    }

    private func handleTaskDispatched(_ p: EngineEventPayload) {
        guard let taskId = p.taskId else { return }
        Task { await taskStore?.updateStatus(taskId: taskId, status: .assigned) }
    }

    private func handleNotification(_ p: EngineEventPayload) {
        latestNotification = AgentNotification(
            title: p.content ?? "Notification",
            message: p.summary ?? "",
            level: p.status ?? "info",
            taskId: p.taskId,
            agentId: p.authorId,
            timestamp: Date()
        )

        // Auto-dismiss after 5 seconds
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(5))
            if latestNotification?.timestamp == latestNotification?.timestamp {
                latestNotification = nil
            }
        }
    }

    private func handlePermissionRequest(_ p: EngineEventPayload) {
        guard let approvalId = p.approvalId,
              !permissionRequests.contains(where: { $0.id == approvalId }) else {
            return
        }

        let requestedAt = Self.parseTimestamp(p.requestedAt) ?? Date()
        permissionRequests.append(PermissionApproval(
            id: approvalId,
            taskId: p.taskId ?? "",
            agentId: p.agentId ?? "agent",
            tool: p.tool ?? "unknown",
            scope: p.scope ?? [:],
            requestedAt: requestedAt,
            expiresAt: Self.parseTimestamp(p.expiresAt) ?? requestedAt.addingTimeInterval(120)
        ))
    }

    private static func parseTimestamp(_ value: String?) -> Date? {
        guard let value else { return nil }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractional.date(from: value) {
            return date
        }
        return ISO8601DateFormatter().date(from: value)
    }
}

// MARK: - Supporting Types

struct TaskProgressInfo {
    let step: Int
    let total: Int
    let summary: String

    var fraction: Double {
        guard total > 0 else { return 0 }
        return Double(step) / Double(total)
    }

    var label: String {
        total > 0 ? "\(step)/\(total) \(summary)" : summary
    }
}

struct PermissionApproval: Identifiable, Equatable {
    enum Decision: String, Equatable {
        case approved
        case denied
    }

    let id: String
    let taskId: String
    let agentId: String
    let tool: String
    let scope: [String: String]
    let requestedAt: Date
    let expiresAt: Date
    var decision: Decision?
    var resolvedAt: Date?
    var resolutionError: String?

    var isPending: Bool {
        decision == nil
    }
}

struct AgentNotification: Identifiable {
    let id = UUID()
    let title: String
    let message: String
    let level: String  // "info" | "success" | "warning" | "error"
    let taskId: String?
    let agentId: String?
    let timestamp: Date

    var color: Color {
        switch level {
        case "success": return .green
        case "warning": return .orange
        case "error": return .red
        default: return .blue
        }
    }

    var icon: String {
        switch level {
        case "success": return "checkmark.circle.fill"
        case "warning": return "exclamationmark.triangle.fill"
        case "error": return "xmark.circle.fill"
        default: return "info.circle.fill"
        }
    }
}

import SwiftUI
