import XCTest

final class StatusPresentationSourceTests: XCTestCase {

    func testStatusStripUsesSharedVocabularyAndAccessibleText() throws {
        let source = try readSource("VoxLog/UI/Chat/AgentStatusStrip.swift")

        XCTAssertTrue(source.contains("struct AgentStatusStrip"))
        XCTAssertTrue(source.contains("struct StatusCountsRow"))
        XCTAssertTrue(source.contains("StatusCountLabel("))
        XCTAssertTrue(source.contains("TF.Status"))
        XCTAssertTrue(source.contains(".monospacedDigit()"))
        XCTAssertTrue(source.contains(".accessibilityLabel("))
        XCTAssertFalse(source.contains("Color.red"))
        XCTAssertFalse(source.contains("Color.orange"))
    }

    func testChatWindowPlacesStatusStripBelowToolbarAndUsesAllTasks() throws {
        let source = try readSource("VoxLog/UI/Chat/ChatWindowView.swift")

        XCTAssertTrue(source.contains("@State private var allTasks: [VoxTask] = []"))
        XCTAssertTrue(source.contains("@Environment(AgentStatusStore.self)"))
        XCTAssertTrue(source.contains("AgentStatusStrip(snapshot: agentStatusStore.snapshot)"))
        XCTAssertTrue(source.contains("allTasks = await taskStore.allTasks()"))
        XCTAssertTrue(source.contains("let taskStore: TaskStore"))
        XCTAssertFalse(source.contains("private var agentStatusSnapshot"))
        XCTAssertFalse(source.contains("withAnimation"))
    }

    func testSidebarRendersStatusAsShapeAndTextWithoutLegacyColors() throws {
        let source = try readSource("VoxLog/UI/Chat/ChatSidebar.swift")

        XCTAssertTrue(source.contains("let statusSnapshot: AgentStatusSnapshot"))
        XCTAssertTrue(source.contains("statusSnapshot.agentSummary(for: agent.id)"))
        XCTAssertTrue(source.contains("agentStatusDetail("))
        XCTAssertTrue(source.contains("summary.status.color"))
        XCTAssertTrue(source.contains("TF.cornerSM"))
        XCTAssertFalse(source.contains("Color.accentColor"))
        XCTAssertFalse(source.contains(".fill(.red)"))
    }

    private func readSource(_ relativePath: String) throws -> String {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return try String(
            contentsOf: packageRoot.appendingPathComponent(relativePath),
            encoding: .utf8
        )
    }
}
