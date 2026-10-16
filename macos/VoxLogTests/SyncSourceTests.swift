import XCTest

final class SyncSourceTests: XCTestCase {
    func testAppStatePrefersNotionSyncWhenEnabled() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/AppState.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("performPreferredSyncSummary()"))
        XCTAssertTrue(source.contains("if NotionSyncSettings.load().isEnabled"))
        XCTAssertTrue(source.contains("NotionSyncEngine.shared.sync().summary"))
        XCTAssertTrue(source.contains("GitSyncEngine.shared.sync().summary"))
    }

    func testGitSyncCommitsLocalDeviceChangesBeforePull() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/Services/GitSyncEngine.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("commitLocalDeviceChangesIfNeeded(message:"))
        XCTAssertTrue(source.contains("git status --porcelain"))
        XCTAssertTrue(source.contains("Pull blocked by local changes outside this device"))
        XCTAssertTrue(source.contains("gitCommitAndPush(message: \"sync: \\(DeviceIdentity.shortName) ↑\\(exported) records\")"))
        XCTAssertFalse(source.contains("if exported > 0 || agentExported"))
    }

    func testSidebarLabelsSyncAsNotionFirst() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Chat/ChatSidebar.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("Notion 优先"))
        XCTAssertFalse(source.contains("Git 增量同步"))
    }
}
