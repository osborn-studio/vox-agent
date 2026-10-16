import XCTest

final class FloatingHUDSourceTests: XCTestCase {
    private var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog")
    }

    func testHUDUsesTheVoxAgentVoiceContractInsteadOfLegacyCapsule() throws {
        let source = try String(
            contentsOf: sourceRoot.appendingPathComponent("UI/FloatingBar/FloatingBarView.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("FloatingHUDMetrics"))
        XCTAssertTrue(source.contains("static let height: CGFloat = 84"))
        XCTAssertTrue(source.contains("TF.barWidth"))
        XCTAssertTrue(source.contains("TF.cornerLG"))
        XCTAssertTrue(source.contains(".ultraThinMaterial"))
        XCTAssertTrue(source.contains("accessibilityReduceMotion"))
        XCTAssertTrue(source.contains("LISTENING"))
        XCTAssertTrue(source.contains("RELEASE TO SEND"))
        XCTAssertTrue(source.contains("ESC CANCEL"))
        XCTAssertTrue(source.contains("TAB SWITCH TARGET"))
        XCTAssertTrue(source.contains("voiceTargetAgentName"))
        XCTAssertTrue(source.contains("level: state.audioLevel"))
        XCTAssertTrue(source.contains("CGFloat(level.current)"))
        XCTAssertFalse(source.contains("Capsule()"))
        XCTAssertFalse(source.contains("LinearGradient"))
        XCTAssertFalse(source.contains("Float.random"))
    }

    func testHUDIsEnabledAndHonorsReduceMotionAtThePanelBoundary() throws {
        let appSource = try String(
            contentsOf: sourceRoot.appendingPathComponent("VoxLogApp.swift"),
            encoding: .utf8
        )
        let panelSource = try String(
            contentsOf: sourceRoot.appendingPathComponent("UI/FloatingBar/FloatingBarPanel.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(appSource.contains("floatingBarController = FloatingBarController(state: appState)"))
        XCTAssertFalse(appSource.contains("// floatingBarController = FloatingBarController(state: appState)"))
        XCTAssertTrue(panelSource.contains("FloatingHUDMetrics.height"))
        XCTAssertTrue(panelSource.contains("accessibilityDisplayShouldReduceMotion"))
    }

    func testGlobalVoiceTargetCanBeChangedWithoutStoppingCapture() throws {
        let hotkeySource = try String(
            contentsOf: sourceRoot.appendingPathComponent("Input/HotkeyManager.swift"),
            encoding: .utf8
        )
        let appSource = try String(
            contentsOf: sourceRoot.appendingPathComponent("VoxLogApp.swift"),
            encoding: .utf8
        )
        let stateSource = try String(
            contentsOf: sourceRoot.appendingPathComponent("UI/AppState.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(hotkeySource.contains("var onTargetCycle"))
        XCTAssertTrue(hotkeySource.contains("keyCode == 48"))
        XCTAssertTrue(appSource.contains("hotkeyManager.onTargetCycle"))
        XCTAssertTrue(appSource.contains("cycleVoiceTarget"))
        XCTAssertTrue(stateSource.contains("var voiceTargetAgentID"))
        XCTAssertTrue(stateSource.contains("var voiceTargetAgentName"))
    }
}
