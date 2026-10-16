import XCTest
@testable import VoxLog

final class OperationsInspectorTests: XCTestCase {
    func testTaskSectionsUseRealLifecycleStates() {
        let now = makeTask("now", .running)
        let paused = makeTask("paused", .blocked)
        let queued = makeTask("queued", .assigned)
        let done = makeTask("done", .completed)
        let failed = makeTask("failed", .failed)

        let groups = OperationsInspectorModel.group([done, queued, now, failed, paused])

        XCTAssertEqual(groups.nowRunning.map(\.id), ["now", "paused"])
        XCTAssertEqual(groups.queueAndSchedule.map(\.id), ["queued"])
        XCTAssertEqual(groups.historyAndAudit.map(\.id), ["failed", "done"])
    }

    func testControlsMatchTaskLifecycle() {
        XCTAssertEqual(
            OperationsInspectorModel.controls(for: .running),
            [.pause, .cancel]
        )
        XCTAssertEqual(
            OperationsInspectorModel.controls(for: .blocked),
            [.resume, .cancel]
        )
        XCTAssertEqual(
            OperationsInspectorModel.controls(for: .assigned),
            [.cancel]
        )
        XCTAssertEqual(
            OperationsInspectorModel.controls(for: .failed),
            [.rerun]
        )
        XCTAssertEqual(
            OperationsInspectorModel.controls(for: .completed),
            []
        )
    }

    func testOperationsInspectorSourceUsesCompactStackedSectionsAndHoverControls() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let inspector = try String(
            contentsOf: root.appendingPathComponent("VoxLog/UI/Chat/OperationsInspector.swift"),
            encoding: .utf8
        )
        let taskStore = try String(
            contentsOf: root.appendingPathComponent("VoxLog/Agent/TaskStore.swift"),
            encoding: .utf8
        )
        let chatWindow = try String(
            contentsOf: root.appendingPathComponent("VoxLog/UI/Chat/ChatWindowView.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(inspector.contains("NOW RUNNING"))
        XCTAssertTrue(inspector.contains("QUEUE & SCHEDULE"))
        XCTAssertTrue(inspector.contains("HISTORY & AUDIT"))
        XCTAssertTrue(inspector.contains(".onHover"))
        XCTAssertTrue(inspector.contains("inspectorRowHeight"))
        XCTAssertTrue(taskStore.contains("\"task_id\": task.id"))
        XCTAssertTrue(taskStore.contains("/v1/tasks/\\(taskId)/\\(action.rawValue)"))
        XCTAssertFalse(chatWindow.contains("Mark done"))
    }

    private func makeTask(_ id: String, _ status: VoxTask.TaskStatus) -> VoxTask {
        VoxTask(
            id: id,
            title: id,
            status: status,
            assignedTo: "agent",
            updatedAt: Date(timeIntervalSince1970: id == "now" || id == "failed" ? 2 : 1)
        )
    }
}
