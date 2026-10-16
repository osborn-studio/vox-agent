import XCTest
@testable import VoxLog

final class MissionControlTests: XCTestCase {

    func testMissionControlPrioritizesLiveWorkAndPendingHumanDecisions() {
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let tasks = [
            task(id: "queued", status: .assigned, agent: "writer", updatedAt: now.addingTimeInterval(-10)),
            task(id: "failed", status: .failed, agent: "reviewer", updatedAt: now),
            task(id: "running-old", status: .running, agent: "writer", updatedAt: now.addingTimeInterval(-20)),
            task(id: "running-new", status: .running, agent: "researcher", updatedAt: now.addingTimeInterval(-5)),
            task(id: "done", status: .completed, agent: "writer", updatedAt: now),
        ]
        let approvals = [
            approval(id: "pending", agent: "writer", expiresAt: now.addingTimeInterval(60)),
            approval(id: "expired", agent: "reviewer", expiresAt: now.addingTimeInterval(-1)),
            approval(id: "resolved", agent: "researcher", expiresAt: now.addingTimeInterval(60), decision: .approved),
        ]

        let model = MissionControlModel(tasks: tasks, approvals: approvals, now: now)

        XCTAssertEqual(model.liveTasks.map(\.id), ["running-new", "running-old", "queued"])
        XCTAssertEqual(model.failedTasks.map(\.id), ["failed"])
        XCTAssertEqual(model.pendingApprovals.map(\.id), ["pending"])
        XCTAssertEqual(model.activeAgentIDs, ["researcher", "writer"])
    }

    func testMissionControlMapsTaskStatesToSharedFunctionalVocabulary() {
        XCTAssertEqual(MissionControlModel.status(for: .running), .running)
        XCTAssertEqual(MissionControlModel.status(for: .failed), .failed)
        XCTAssertEqual(MissionControlModel.status(for: .completed), .completed)
        XCTAssertEqual(MissionControlModel.status(for: .assigned), .idle)
        XCTAssertEqual(MissionControlModel.status(for: .blocked), .idle)
    }

    func testMissionControlRemainsAvailableAndUsesRealOperationalData() throws {
        let window = try source("VoxLog/UI/Chat/ChatWindowView.swift")
        let sidebar = try source("VoxLog/UI/Chat/ChatSidebar.swift")
        let missionControl = try source("VoxLog/UI/Chat/MissionControlView.swift")

        XCTAssertTrue(window.contains("MissionControlNavigation.workspace(selectedWorkspace, enabled: showMissionControl)"))
        XCTAssertTrue(sidebar.contains("if showMissionControl {"))
        XCTAssertTrue(window.contains("MissionControlView("))
        XCTAssertTrue(window.contains("tasks: allTasks"))
        XCTAssertTrue(window.contains("approvals: engine?.eventHandler.permissionRequests ?? []"))
        XCTAssertTrue(sidebar.contains("@Binding var selectedWorkspace: AgentWorkspace"))
        XCTAssertTrue(sidebar.contains("workspaceRow(.missionControl"))
        XCTAssertTrue(sidebar.contains("workspaceRow(.command"))
        XCTAssertTrue(missionControl.contains("AgentStatusStrip(snapshot: snapshot"))
        XCTAssertTrue(missionControl.contains("model.liveTasks"))
        XCTAssertTrue(missionControl.contains("model.pendingApprovals"))
        XCTAssertTrue(missionControl.contains("snapshot.agentSummary(for: agent.id)"))
        XCTAssertTrue(missionControl.contains(".accessibilityLabel("))
        XCTAssertFalse(missionControl.contains("Color.red"))
        XCTAssertFalse(missionControl.contains("Color.orange"))
        XCTAssertFalse(missionControl.contains("Color.blue"))
    }

    private func task(
        id: String,
        status: VoxTask.TaskStatus,
        agent: String,
        updatedAt: Date
    ) -> VoxTask {
        VoxTask(
            id: id,
            title: id,
            status: status,
            assignedTo: agent,
            createdAt: updatedAt,
            updatedAt: updatedAt
        )
    }

    private func approval(
        id: String,
        agent: String,
        expiresAt: Date,
        decision: PermissionApproval.Decision? = nil
    ) -> PermissionApproval {
        PermissionApproval(
            id: id,
            taskId: "task-\(id)",
            agentId: agent,
            tool: "filesystem.read",
            scope: ["path": "/tmp/example"],
            requestedAt: expiresAt.addingTimeInterval(-30),
            expiresAt: expiresAt,
            decision: decision
        )
    }

    private func source(_ relativePath: String) throws -> String {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return try String(
            contentsOf: packageRoot.appendingPathComponent(relativePath),
            encoding: .utf8
        )
    }
}
