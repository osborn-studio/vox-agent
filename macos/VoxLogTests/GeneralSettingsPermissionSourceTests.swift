import XCTest

final class GeneralSettingsPermissionSourceTests: XCTestCase {
    func testGeneralSettingsUsesSharedPermissionManagerForAccessibility() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Settings/GeneralSettingsTab.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("PermissionManager.hasAccessibilityPermission"))
        XCTAssertTrue(source.contains("NSApplication.didBecomeActiveNotification"))
        XCTAssertTrue(source.contains("startPermissionRefreshPolling()"))
        XCTAssertTrue(source.contains("Task.sleep(for: .milliseconds(500))"))
        XCTAssertFalse(source.contains("AXIsProcessTrusted()"))
        XCTAssertFalse(source.contains("AXIsProcessTrustedWithOptions"))
    }

    func testSetupWizardUsesSharedPermissionManagerForAccessibility() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Setup/SetupWizardView.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("PermissionManager.hasMicrophonePermission"))
        XCTAssertTrue(source.contains("PermissionManager.hasAccessibilityPermission"))
        XCTAssertFalse(source.contains("AXIsProcessTrusted()"))
    }
}
