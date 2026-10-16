import XCTest

final class ChatWindowSourceTests: XCTestCase {
    func testChatWindowDoesNotRenderGlobalWorkspaceHeader() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Chat/ChatWindowView.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertFalse(source.contains("workspaceHeader"))
        XCTAssertFalse(source.contains("LanguageSwitcher(compact: true)"))
        XCTAssertFalse(source.contains("statusPill("))
        XCTAssertFalse(source.contains("单团队工作区"))
        XCTAssertFalse(source.contains("collapsedLeftSidebar"))
        XCTAssertFalse(source.contains("collapsedInspector"))
        XCTAssertFalse(source.contains("collapsedSidebarWidth"))
        XCTAssertFalse(source.contains(".frame(minWidth: 720"))
        XCTAssertTrue(source.contains(".frame(minWidth: windowMinWidth"))
        XCTAssertTrue(source.contains("ChatLayoutMetrics.windowMinWidth"))
        XCTAssertTrue(source.contains("isLeftSidebarCollapsed.toggle()"))
        XCTAssertTrue(source.contains("isInspectorCollapsed.toggle()"))
        XCTAssertTrue(source.contains("private var chatLayout: some View"))
        XCTAssertTrue(source.contains("if isLeftSidebarCollapsed && isInspectorCollapsed"))
        XCTAssertTrue(source.contains("centerPane\n        } else {\n            HSplitView"))
        XCTAssertTrue(source.contains("leftSidebarControl"))
        XCTAssertTrue(source.contains("Spacer(minLength: 0)\n\n                rightSidebarControl"))
    }

    func testConversationUsesSingleScrollContainerWithJumpToBottomControl() throws {
        let chatWindowURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Chat/ChatWindowView.swift")
        let chatAreaURL = chatWindowURL
            .deletingLastPathComponent()
            .appendingPathComponent("ChatArea.swift")
        let chatWindowSource = try String(contentsOf: chatWindowURL, encoding: .utf8)
        let chatAreaSource = try String(contentsOf: chatAreaURL, encoding: .utf8)

        XCTAssertTrue(chatWindowSource.contains("ScrollViewReader"))
        XCTAssertTrue(chatWindowSource.contains("scrollToBottomButton"))
        XCTAssertTrue(chatWindowSource.contains("conversationContentMaxWidth"))
        XCTAssertTrue(chatWindowSource.contains("conversationContentHorizontalInset"))
        XCTAssertTrue(chatWindowSource.contains(".frame(maxWidth: .infinity, alignment: .center)"))
        XCTAssertTrue(chatWindowSource.contains("conversation-bottom"))
        XCTAssertFalse(chatAreaSource.contains("ScrollViewReader"))
        XCTAssertFalse(chatAreaSource.contains("ScrollView {"))
    }

    func testComposerUsesFullStableWindowWidth() throws {
        let chatWindowURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Chat/ChatWindowView.swift")
        let source = try String(contentsOf: chatWindowURL, encoding: .utf8)

        XCTAssertTrue(source.contains("ChatInputBar("))
        XCTAssertTrue(source.contains(".frame(minWidth: ChatLayoutMetrics.conversationMinWidth, maxWidth: .infinity)"))
        XCTAssertFalse(source.contains("ChatInputBar(\n                textInput: $textInput,\n                selectedAgent: selectedAgent,\n                onSend: { text in\n                    saveTextMessage(text)\n                }\n            )\n            .frame(maxWidth: ChatLayoutMetrics.conversationContentMaxWidth)"))
    }

    func testSidebarFooterHasVisibleOnlineUpdateButton() throws {
        let sidebarURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Chat/ChatSidebar.swift")
        let settingsURL = sidebarURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Settings/SettingsView.swift")
        let appStateURL = sidebarURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("AppState.swift")
        let sidebarSource = try String(contentsOf: sidebarURL, encoding: .utf8)
        let settingsSource = try String(contentsOf: settingsURL, encoding: .utf8)
        let appStateSource = try String(contentsOf: appStateURL, encoding: .utf8)

        XCTAssertTrue(sidebarSource.contains("Image(systemName: \"arrow.down.circle\")"))
        XCTAssertTrue(sidebarSource.contains("UpdateChecker.shared.checkNow(appState: appState)"))
        XCTAssertTrue(sidebarSource.contains("NotificationCenter.default.post(name: .navigateToAbout"))
        XCTAssertTrue(sidebarSource.contains("在线更新"))
        XCTAssertTrue(settingsSource.contains(".navigateToAbout"))
        XCTAssertTrue(settingsSource.contains("selectedTab = .about"))
        XCTAssertTrue(appStateSource.contains("navigateToAbout"))
    }
}
