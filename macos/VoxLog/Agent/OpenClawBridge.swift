import Foundation
import os

/// Bridge between VoxLog and OpenClaw (小龙虾) for task dispatch.
///
/// When a VoxLog Agent is bound to an OpenClaw workspace agent,
/// tasks assigned to that agent can be dispatched to OpenClaw for execution.
actor OpenClawBridge {

    static let shared = OpenClawBridge()
    private let logger = Logger(subsystem: "com.voxlog.openclaw", category: "OpenClawBridge")

    private var bindings: [String: OpenClawBinding] = [:]  // keyed by voxlogAgentId
    private let bindingsURL: URL

    init() {
        let appSupport = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first!.appendingPathComponent("VoxLog", isDirectory: true)
        try? FileManager.default.createDirectory(at: appSupport, withIntermediateDirectories: true)
        bindingsURL = appSupport.appendingPathComponent("openclaw-bindings.json")
        loadBindings()
    }

    // MARK: - Binding Management

    func bind(voxlogAgentId: String, workspace: String, agentRef: String) {
        let binding = OpenClawBinding(
            voxlogAgentId: voxlogAgentId,
            openclawWorkspace: workspace,
            openclawAgentRef: agentRef,
            boundAt: Date()
        )
        bindings[voxlogAgentId] = binding
        saveBindings()
        logger.info("Bound \(voxlogAgentId) → OpenClaw \(workspace)/\(agentRef)")
    }

    func unbind(voxlogAgentId: String) {
        bindings.removeValue(forKey: voxlogAgentId)
        saveBindings()
        logger.info("Unbound \(voxlogAgentId)")
    }

    func binding(for agentId: String) -> OpenClawBinding? {
        bindings[agentId]
    }

    func allBindings() -> [OpenClawBinding] {
        Array(bindings.values)
    }

    func isBound(_ agentId: String) -> Bool {
        bindings[agentId] != nil
    }

    // MARK: - Task Dispatch

    /// Dispatch a task to OpenClaw for execution.
    /// Returns true if dispatch succeeded.
    func dispatch(task: VoxTask) async -> Bool {
        guard let binding = bindings[task.assignedTo] else {
            logger.warning("No OpenClaw binding for agent \(task.assignedTo)")
            return false
        }

        guard let url = URL(string: binding.dispatchURL) else { return false }

        let payload: [String: Any] = [
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "priority": task.priority.rawValue,
            "source": "voxlog",
            "source_agent": task.sourceAgent ?? "",
            "assigned_to": binding.openclawAgentRef,
            "workspace": binding.openclawWorkspace,
        ]

        do {
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: payload)
            request.timeoutInterval = 10

            let (_, response) = try await URLSession.shared.data(for: request)
            let httpResponse = response as? HTTPURLResponse
            let success = httpResponse?.statusCode == 200 || httpResponse?.statusCode == 201

            if success {
                logger.info("Task dispatched to OpenClaw: \(task.title) → \(binding.openclawAgentRef)")
            } else {
                logger.warning("OpenClaw dispatch failed: HTTP \(httpResponse?.statusCode ?? 0)")
            }
            return success
        } catch {
            logger.error("OpenClaw dispatch error: \(error)")
            return false
        }
    }

    /// Check if OpenClaw is running locally.
    func isOpenClawAvailable() async -> Bool {
        guard let url = URL(string: "http://127.0.0.1:7860/health") else { return false }
        do {
            let (_, response) = try await URLSession.shared.data(from: url)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    // MARK: - Persistence

    private nonisolated func loadBindings() {
        // Will be loaded on first access
    }

    func ensureLoaded() {
        if bindings.isEmpty {
            if let data = try? Data(contentsOf: bindingsURL),
               let saved = try? JSONDecoder().decode([String: OpenClawBinding].self, from: data) {
                bindings = saved
            }
        }
    }

    private func saveBindings() {
        if let data = try? JSONEncoder().encode(bindings) {
            try? data.write(to: bindingsURL, options: .atomic)
        }
    }
}
