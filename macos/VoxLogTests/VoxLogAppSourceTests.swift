import XCTest

final class VoxLogAppSourceTests: XCTestCase {
    func testUserFacingSurfacesUseVoxAgentBrandAndCockpitPositioning() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let relativePaths = [
            "VoxLog/VoxLogApp.swift",
            "VoxLog/UI/Setup/SetupWizardView.swift",
            "VoxLog/UI/Settings/AboutTab.swift",
            "VoxLog/UI/Settings/SyncSettingsTab.swift",
        ]
        let sources = try relativePaths.map {
            try String(contentsOf: root.appendingPathComponent($0), encoding: .utf8)
        }
        let combined = sources.joined(separator: "\n")

        XCTAssertTrue(combined.contains("About Vox Agent"))
        XCTAssertTrue(combined.contains("agent cockpit"))
        XCTAssertTrue(combined.contains("Find Vox Agent in System Settings"))
        XCTAssertTrue(combined.contains("Mirror Vox Agent conversations"))
        XCTAssertFalse(combined.contains("About VoxLog"))
        XCTAssertFalse(combined.contains("VoxLog will close"))
        XCTAssertFalse(combined.contains("Find VoxLog in System Settings"))
        XCTAssertFalse(combined.contains("Mirror VoxLog chat history"))
        XCTAssertFalse(combined.contains("VoxLog icon"))
    }

    func testMainChatWindowUsesStableMinimumContentWidth() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/VoxLogApp.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains(".defaultSize(width: ChatLayoutMetrics.windowStableMinWidth"))
        XCTAssertTrue(source.contains(".windowResizability(.contentSize)"))
    }

    func testMenuBarAndChatUseOneSharedStatusStore() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/VoxLogApp.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("let taskStore = TaskStore()"))
        XCTAssertTrue(source.contains("let agentStatusStore = AgentStatusStore()"))
        XCTAssertTrue(source.contains("ChatWindowView(taskStore: appDelegate.taskStore)"))
        XCTAssertGreaterThanOrEqual(
            source.components(separatedBy: ".environment(appDelegate.agentStatusStore)").count - 1,
            3
        )
        XCTAssertTrue(source.contains("MenuBarStatusLabel()"))
        XCTAssertTrue(source.contains("if let badgeStatus"))
        XCTAssertTrue(source.contains("Circle().fill(badgeStatus.color)"))
        XCTAssertTrue(source.contains("onAwaitingApproval: focusPendingApproval"))
        XCTAssertTrue(source.contains("name: .focusPermissionApproval"))
        XCTAssertFalse(source.contains("resolveApproval(id:"))
        XCTAssertTrue(source.contains("startAgentStatusRefresh()"))
        XCTAssertTrue(source.contains("await self.taskStore.ensureLoaded()"))
        XCTAssertTrue(source.contains("HStack(spacing: TF.spacingXS)"))
        XCTAssertTrue(source.contains(".frame(width: TF.spacingSM, height: TF.spacingSM)"))
        XCTAssertTrue(source.contains("Button(L(\"退出 Vox Agent\", \"Quit Vox Agent\"))"))
        XCTAssertFalse(source.contains("case .error: return TF.settingsAccentRed"))
    }
}
