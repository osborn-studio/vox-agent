import Foundation
import os

enum EngineTaskAction: String, Equatable, Sendable {
    case pause
    case resume
    case cancel
    case rerun
}

enum TaskStoreError: LocalizedError {
    case engineUnavailable
    case invalidResponse
    case controlFailed(String)

    var errorDescription: String? {
        switch self {
        case .engineUnavailable:
            return L("Agent 引擎未连接", "Agent engine is not connected")
        case .invalidResponse:
            return L("Agent 引擎返回了无效响应", "Agent engine returned an invalid response")
        case .controlFailed(let detail):
            return detail
        }
    }
}

/// Persists tasks to JSON file. Supports CRUD + status transitions.
actor TaskStore {

    private var tasks: [VoxTask] = []
    private let fileURL: URL
    private let logger = Logger(subsystem: "com.voxlog.tasks", category: "TaskStore")

    init(fileURL: URL? = nil) {
        let defaultDirectory = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first!.appendingPathComponent("VoxLog", isDirectory: true)
        let resolvedFileURL = fileURL ?? defaultDirectory.appendingPathComponent("tasks.json")
        try? FileManager.default.createDirectory(
            at: resolvedFileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        self.fileURL = resolvedFileURL
        load()
    }

    // MARK: - Read

    func allTasks() -> [VoxTask] { tasks }

    func tasksForAgent(_ agentId: String) -> [VoxTask] {
        tasks.filter { $0.assignedTo == agentId }
    }

    func activeTasks() -> [VoxTask] {
        tasks.filter { $0.isActive }
    }

    func task(byId id: String) -> VoxTask? {
        tasks.first { $0.id == id }
    }

    /// Replace the local compatibility cache with the server-authoritative task set.
    func reconcileEngineTasks(_ canonicalTasks: [VoxTask]) {
        tasks = canonicalTasks
        save()
    }

    // MARK: - Create

    func createTask(title: String, assignedTo: String, description: String = "",
                    priority: VoxTask.TaskPriority = .medium,
                    sourceMessageId: String? = nil, sourceAgent: String? = nil) -> VoxTask {
        let task = VoxTask(
            title: title,
            description: description,
            status: .assigned,
            priority: priority,
            assignedTo: assignedTo,
            sourceMessageId: sourceMessageId,
            sourceAgent: sourceAgent
        )
        tasks.append(task)
        save()
        logger.info("Task created: \(task.title) → \(task.assignedTo)")
        return task
    }

    // MARK: - Update Status

    func updateStatus(id: String, to newStatus: VoxTask.TaskStatus) {
        guard let idx = tasks.firstIndex(where: { $0.id == id }) else { return }
        tasks[idx].status = newStatus
        tasks[idx].updatedAt = Date()
        save()
        logger.info("Task \(id) → \(newStatus.rawValue)")
    }

    /// Convenience for EngineEventHandler (uses taskId: instead of id:)
    func updateStatus(taskId: String, status: VoxTask.TaskStatus) {
        updateStatus(id: taskId, to: status)
    }

    // MARK: - Engine Integration

    /// Dispatch a task to the Python Agent Engine via REST API.
    /// Falls back to OpenClaw if engine is unavailable.
    func dispatchToEngine(
        task: VoxTask,
        enginePort: Int,
        provider: String = "claude"
    ) async -> Bool {
        let url = URL(string: "http://127.0.0.1:\(enginePort)/v1/tasks")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 10

        let body: [String: Any] = [
            "task_id": task.id,
            "agent_id": task.assignedTo,
            "title": task.title,
            "prompt": task.description.isEmpty ? task.title : task.description,
            "priority": priorityToInt(task.priority),
            "trigger_message_id": task.sourceMessageId ?? "",
            "source_agent": task.sourceAgent ?? "",
            "provider": provider,
        ]

        guard let jsonData = try? JSONSerialization.data(withJSONObject: body) else {
            return false
        }
        request.httpBody = jsonData

        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            if let httpResponse = response as? HTTPURLResponse,
               httpResponse.statusCode == 200 {
                logger.info("Task dispatched to engine: \(task.id)")
                return true
            }
        } catch {
            logger.error("Engine dispatch failed: \(error.localizedDescription)")
        }
        return false
    }

    func controlEngineTask(
        taskId: String,
        action: EngineTaskAction,
        enginePort: Int
    ) async throws {
        guard action != .rerun else {
            throw TaskStoreError.controlFailed(
                L("重跑会创建一个新任务", "Rerun creates a new task")
            )
        }
        guard let url = URL(
            string: "http://127.0.0.1:\(enginePort)/v1/tasks/\(taskId)/\(action.rawValue)"
        ) else {
            throw TaskStoreError.invalidResponse
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 10
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw TaskStoreError.invalidResponse
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw TaskStoreError.controlFailed(
                detail ?? L("任务控制失败", "Task control failed")
            )
        }

        switch action {
        case .pause:
            updateStatus(id: taskId, to: .blocked)
        case .resume:
            updateStatus(id: taskId, to: .running)
        case .cancel:
            updateStatus(id: taskId, to: .cancelled)
        case .rerun:
            break
        }
    }

    func rerunTask(_ task: VoxTask, enginePort: Int) async throws -> VoxTask {
        let replacement = createTask(
            title: task.title,
            assignedTo: task.assignedTo,
            description: task.description,
            priority: task.priority,
            sourceMessageId: task.sourceMessageId,
            sourceAgent: task.sourceAgent
        )
        guard await dispatchToEngine(task: replacement, enginePort: enginePort) else {
            deleteTask(id: replacement.id)
            throw TaskStoreError.controlFailed(
                L("无法重新派发任务", "Could not redispatch task")
            )
        }
        updateStatus(id: replacement.id, to: .running)
        return tasks.first(where: { $0.id == replacement.id }) ?? replacement
    }

    private func priorityToInt(_ p: VoxTask.TaskPriority) -> Int {
        switch p {
        case .low: return 0
        case .medium: return 1
        case .high: return 2
        case .urgent: return 3
        }
    }

    // MARK: - Delete

    func deleteTask(id: String) {
        tasks.removeAll { $0.id == id }
        save()
    }

    // MARK: - Persistence

    private nonisolated func load() {
        // Called from init, safe because actor not yet shared
    }

    func ensureLoaded() {
        if tasks.isEmpty {
            if let data = try? Data(contentsOf: fileURL),
               let saved = try? JSONDecoder().decode([VoxTask].self, from: data) {
                tasks = saved
            }
        }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(tasks) {
            try? data.write(to: fileURL, options: .atomic)
        }
    }
}
