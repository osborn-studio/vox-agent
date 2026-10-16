import XCTest

final class ConversationTimelineSourceTests: XCTestCase {
    func testConversationUsesOrderedTimelineInsteadOfSeparateBubbleStacks() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let source = try String(
            contentsOf: packageRoot.appendingPathComponent("VoxLog/UI/Chat/ChatWindowView.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("private var conversationTimelineItems"))
        XCTAssertTrue(source.contains(".sorted { lhs, rhs in"))
        XCTAssertTrue(source.contains("case .voice(let message)"))
        XCTAssertTrue(source.contains("case .agent(let message)"))
        XCTAssertTrue(source.contains("case .permission(let approval)"))
        XCTAssertFalse(source.contains("ForEach(agentMessages)"))
    }

    func testUserAndAgentTurnsFollowDesignContractWithoutBubbles() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let userTurn = try String(
            contentsOf: packageRoot.appendingPathComponent("VoxLog/UI/Chat/ChatArea.swift"),
            encoding: .utf8
        )
        let agentTurnURL = packageRoot
            .appendingPathComponent("VoxLog/UI/Chat/AgentTimelineTurn.swift")
        let agentTurn = try String(contentsOf: agentTurnURL, encoding: .utf8)

        XCTAssertTrue(userTurn.contains("Rectangle().fill(TF.accent).frame(width: 2)"))
        XCTAssertTrue(userTurn.contains("Image(systemName: \"waveform\")"))
        XCTAssertFalse(userTurn.contains("RoundedRectangle"))
        XCTAssertFalse(userTurn.contains("messageBubble"))

        XCTAssertTrue(agentTurn.contains("struct AgentTimelineTurn"))
        XCTAssertTrue(agentTurn.contains("design: .monospaced"))
        XCTAssertTrue(agentTurn.contains("isToolOutputExpanded"))
        XCTAssertFalse(agentTurn.contains("RoundedRectangle"))
        XCTAssertFalse(agentTurn.contains("AgentMessageBubble"))
        XCTAssertFalse(
            FileManager.default.fileExists(
                atPath: packageRoot.appendingPathComponent("VoxLog/UI/Chat/AgentMessageBubble.swift").path
            )
        )
    }
}
