import Foundation

// MARK: - Task

/// A task created from a voice message, assigned to an agent.
///
/// State machine:
/// ```
/// draft → planned → assigned → running → completed
///                                    ↘ → blocked → running
///                                    ↘ → failed
///                      ↘ → cancelled
/// ```
struct VoxTask: Codable, Identifiable, Hashable {
    let id: String
    var title: String
    var description: String
    var status: TaskStatus
    var priority: TaskPriority
    var assignedTo: String          // Agent id
    var sourceMessageId: String?    // The message that spawned this task
    var sourceAgent: String?        // Agent where the message was said
    let createdAt: Date
    var updatedAt: Date

    enum TaskStatus: String, Codable, CaseIterable {
        case draft
        case planned
        case assigned
        case running
        case blocked
        case completed
        case failed
        case cancelled
    }

    enum TaskPriority: String, Codable, CaseIterable {
        case low
        case medium
        case high
        case urgent
    }

    init(
        id: String = UUID().uuidString,
        title: String,
        description: String = "",
        status: TaskStatus = .draft,
        priority: TaskPriority = .medium,
        assignedTo: String,
        sourceMessageId: String? = nil,
        sourceAgent: String? = nil,
        createdAt: Date = Date(),
        updatedAt: Date = Date()
    ) {
        self.id = id; self.title = title; self.description = description
        self.status = status; self.priority = priority; self.assignedTo = assignedTo
        self.sourceMessageId = sourceMessageId; self.sourceAgent = sourceAgent
        self.createdAt = createdAt; self.updatedAt = updatedAt
    }

    var statusEmoji: String {
        switch status {
        case .draft: return "📝"
        case .planned: return "📋"
        case .assigned: return "📌"
        case .running: return "🔄"
        case .blocked: return "🚫"
        case .completed: return "✅"
        case .failed: return "❌"
        case .cancelled: return "🗑️"
        }
    }

    var isActive: Bool {
        [.draft, .planned, .assigned, .running, .blocked].contains(status)
    }
}

// MARK: - OpenClaw Binding

/// Represents a binding between a VoxLog Agent and an OpenClaw workspace agent.
struct OpenClawBinding: Codable, Hashable {
    let voxlogAgentId: String
    let openclawWorkspace: String    // e.g. "personal"
    let openclawAgentRef: String     // e.g. "code-reviewer"
    let boundAt: Date

    /// URL for dispatching tasks to OpenClaw
    var dispatchURL: String {
        "http://127.0.0.1:7860/v1/tasks"  // OpenClaw local API
    }
}
