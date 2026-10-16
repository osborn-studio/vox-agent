import Foundation

struct AgentStatusSnapshot: Equatable {

    struct AgentSummary: Equatable {
        let status: TF.Status
        let runningTaskTitle: String?
        let runningCount: Int
        let queuedCount: Int
        let awaitingApprovalCount: Int
        let failedCount: Int
    }

    let runningCount: Int
    let queuedCount: Int
    let awaitingApprovalCount: Int
    let failedCount: Int
    let engineStatus: TF.Status

    private let tasksByAgent: [String: [VoxTask]]
    private let pendingApprovalIDs: Set<String>

    init(
        tasks: [VoxTask],
        pendingApprovalIDs: Set<String>,
        engineState: EngineCoordinator.EngineState
    ) {
        runningCount = tasks.count { $0.status == .running }
        queuedCount = tasks.count { Self.isQueued($0.status) }
        awaitingApprovalCount = pendingApprovalIDs.count
        failedCount = tasks.count { $0.status == .failed }
        engineStatus = Self.status(for: engineState)
        tasksByAgent = Dictionary(grouping: tasks, by: \.assignedTo)
        self.pendingApprovalIDs = pendingApprovalIDs
    }

    var primaryStatus: TF.Status {
        if awaitingApprovalCount > 0 { return .awaitingApproval }
        if failedCount > 0 || engineStatus == .failed { return .failed }
        if runningCount > 0 { return .running }
        return .idle
    }

    func agentSummary(for agentID: String) -> AgentSummary {
        let tasks = tasksByAgent[agentID] ?? []
        let awaitingCount = tasks.count { pendingApprovalIDs.contains($0.id) }
        let failures = tasks.filter { $0.status == .failed }
        let running = tasks.filter { $0.status == .running }
        let queuedCount = tasks.count { Self.isQueued($0.status) }

        let status: TF.Status
        if awaitingCount > 0 {
            status = .awaitingApproval
        } else if !failures.isEmpty {
            status = .failed
        } else if !running.isEmpty {
            status = .running
        } else {
            status = .idle
        }

        return AgentSummary(
            status: status,
            runningTaskTitle: running.first?.title,
            runningCount: running.count,
            queuedCount: queuedCount,
            awaitingApprovalCount: awaitingCount,
            failedCount: failures.count
        )
    }

    private static func isQueued(_ status: VoxTask.TaskStatus) -> Bool {
        switch status {
        case .draft, .planned, .assigned, .blocked:
            return true
        case .running, .completed, .failed, .cancelled:
            return false
        }
    }

    private static func status(for engineState: EngineCoordinator.EngineState) -> TF.Status {
        switch engineState {
        case .running:
            return .running
        case .error:
            return .failed
        case .starting, .stopped:
            return .idle
        }
    }
}

@MainActor
@Observable
final class AgentStatusStore {
    private(set) var snapshot = AgentStatusSnapshot(
        tasks: [],
        pendingApprovalIDs: [],
        engineState: .stopped
    )

    func update(
        tasks: [VoxTask],
        pendingApprovalIDs: Set<String>,
        engineState: EngineCoordinator.EngineState
    ) {
        let next = AgentStatusSnapshot(
            tasks: tasks,
            pendingApprovalIDs: pendingApprovalIDs,
            engineState: engineState
        )
        if next != snapshot {
            snapshot = next
        }
    }

    func refresh(taskStore: TaskStore, engine: EngineCoordinator) async {
        let tasks = await taskStore.allTasks()
        update(
            tasks: tasks,
            pendingApprovalIDs: Set(engine.eventHandler.pendingApprovals.map(\.taskId)),
            engineState: engine.state
        )
    }
}
