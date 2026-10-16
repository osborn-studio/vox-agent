import XCTest
@testable import VoxLog

final class AgentStatusSnapshotTests: XCTestCase {

    func testFunctionalStatusVocabularyHasLabelsAndDistinctSymbols() {
        let previousLanguage = UserDefaults.standard.string(forKey: "tf_language")
        UserDefaults.standard.set("en", forKey: "tf_language")
        defer { UserDefaults.standard.set(previousLanguage, forKey: "tf_language") }

        XCTAssertEqual(TF.Status.running.label, "Running")
        XCTAssertEqual(TF.Status.awaitingApproval.label, "Awaiting approval")
        XCTAssertEqual(TF.Status.failed.label, "Failed")
        XCTAssertEqual(TF.Status.idle.label, "Idle")
        XCTAssertEqual(TF.Status.completed.label, "Completed")
        XCTAssertEqual(Set(TF.Status.allCases.map(\.symbolName)).count, TF.Status.allCases.count)

        _ = TF.warning
        _ = TF.danger
        _ = TF.idle
    }

    func testSnapshotCountsTasksAndPendingHumanActions() {
        let tasks = [
            makeTask(id: "run", status: .running, agent: "alpha"),
            makeTask(id: "approval", status: .assigned, agent: "alpha"),
            makeTask(id: "failure", status: .failed, agent: "beta"),
            makeTask(id: "blocked", status: .blocked, agent: "gamma"),
            makeTask(id: "done", status: .completed, agent: "gamma"),
        ]

        let snapshot = AgentStatusSnapshot(
            tasks: tasks,
            pendingApprovalIDs: ["approval"],
            engineState: .running
        )

        XCTAssertEqual(snapshot.runningCount, 1)
        XCTAssertEqual(snapshot.queuedCount, 2)
        XCTAssertEqual(snapshot.awaitingApprovalCount, 1)
        XCTAssertEqual(snapshot.failedCount, 1)
        XCTAssertEqual(snapshot.engineStatus, .running)
    }

    func testAgentStatusPrioritizesApprovalThenFailureThenRunning() {
        let tasks = [
            makeTask(id: "run", status: .running, agent: "alpha", title: "Ship release"),
            makeTask(id: "failure", status: .failed, agent: "alpha"),
            makeTask(id: "approval", status: .assigned, agent: "alpha"),
        ]

        let awaiting = AgentStatusSnapshot(
            tasks: tasks,
            pendingApprovalIDs: ["approval"],
            engineState: .running
        )
        XCTAssertEqual(awaiting.agentSummary(for: "alpha").status, .awaitingApproval)

        let failed = AgentStatusSnapshot(
            tasks: tasks,
            pendingApprovalIDs: [],
            engineState: .running
        )
        XCTAssertEqual(failed.agentSummary(for: "alpha").status, .failed)

        let running = AgentStatusSnapshot(
            tasks: [makeTask(id: "run", status: .running, agent: "alpha", title: "Ship release")],
            pendingApprovalIDs: [],
            engineState: .running
        )
        let runningSummary = running.agentSummary(for: "alpha")
        XCTAssertEqual(runningSummary.status, .running)
        XCTAssertEqual(runningSummary.runningTaskTitle, "Ship release")
        XCTAssertEqual(running.agentSummary(for: "missing").status, .idle)
    }

    func testPrimaryStatusIncludesEngineFailure() {
        let failed = AgentStatusSnapshot(tasks: [], pendingApprovalIDs: [], engineState: .error)
        let idle = AgentStatusSnapshot(tasks: [], pendingApprovalIDs: [], engineState: .stopped)

        XCTAssertEqual(failed.primaryStatus, .failed)
        XCTAssertEqual(idle.primaryStatus, .idle)
    }

    @MainActor
    func testStatusStorePublishesOneSharedSnapshot() {
        let store = AgentStatusStore()
        let tasks = [makeTask(id: "run", status: .running, agent: "alpha")]

        store.update(tasks: tasks, pendingApprovalIDs: ["run"], engineState: .running)

        XCTAssertEqual(store.snapshot.runningCount, 1)
        XCTAssertEqual(store.snapshot.awaitingApprovalCount, 1)
        XCTAssertEqual(store.snapshot.primaryStatus, .awaitingApproval)
    }

    func testStatusStoreRefreshDoesNotReloadPersistenceOnEveryPoll() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/AgentStatusSnapshot.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertFalse(source.contains("await taskStore.ensureLoaded()"))
    }

    private func makeTask(
        id: String,
        status: VoxTask.TaskStatus,
        agent: String,
        title: String = "Task"
    ) -> VoxTask {
        VoxTask(id: id, title: title, status: status, assignedTo: agent)
    }
}
