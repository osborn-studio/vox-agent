import XCTest

final class ChatInputBarSourceTests: XCTestCase {
    func testComposerUsesRecognitionSessionAsItsOnlyAudioOwner() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Chat/ChatInputBar.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("delegate.session.startRecording("))
        XCTAssertTrue(source.contains("outputDestination: .composer"))
        XCTAssertTrue(source.contains("@State private var ownsVoiceSession = false"))
        XCTAssertTrue(source.contains("guard ownsVoiceSession else { return }"))
        XCTAssertTrue(source.contains("delegate.session.stopRecording()"))
        XCTAssertTrue(source.contains("delegate.session.cancelRecording()"))
        XCTAssertTrue(source.contains("appState.audioLevel.current"))
        XCTAssertFalse(source.contains("QwenFlashStreamASR"))
        XCTAssertFalse(source.contains("getRecordedAudioData"))
        XCTAssertFalse(source.contains("forceReset"))
        XCTAssertFalse(source.contains("@State private var isRecording"))
    }

    func testChatInputSupportsShiftEnterNewlineAndEnterSubmit() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Chat/ChatInputBar.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("SubmitAwareTextView"))
        XCTAssertTrue(source.contains("event.keyCode == 36 || event.keyCode == 76"))
        XCTAssertTrue(source.contains("!event.modifierFlags.contains(.shift)"))
        XCTAssertTrue(source.contains("onSubmit?()"))
        XCTAssertTrue(source.contains("event.keyCode == 53"))
        XCTAssertTrue(source.contains("onEscape?()"))
        XCTAssertTrue(source.contains("super.keyDown(with: event)"))
    }

    func testComposerKeepsMicAndSendButtonsInsideInputAtNarrowWidths() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Chat/ChatInputBar.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("VoiceWaveform("))
        XCTAssertTrue(source.contains("VoiceCountdownRing("))
        XCTAssertTrue(source.contains("accessibilityReduceMotion"))
        XCTAssertTrue(source.contains("TF.recording"))
        XCTAssertTrue(source.contains("TF.amber"))
        XCTAssertTrue(source.contains("VoiceInputStateMachine"))
        XCTAssertTrue(source.contains("GeometryReader { geometry in"))
        XCTAssertTrue(source.contains("composerBody(width: max(0, geometry.size.width - ChatLayoutMetrics.inputHorizontalInset * 2))"))
        XCTAssertTrue(source.contains(".frame(width: width)"))
        XCTAssertTrue(source.contains("ChatLayoutMetrics.voiceInputExpandedHeight"))
        XCTAssertTrue(source.contains(".padding(.trailing, ChatLayoutMetrics.inputButtonReserveWidth)"))
        XCTAssertTrue(source.contains(".fixedSize()"))
        XCTAssertTrue(source.contains(".layoutPriority(10)"))
        XCTAssertTrue(source.contains(".padding(.trailing, 10)"))
        XCTAssertTrue(source.contains("composerIconButton("))
        XCTAssertTrue(source.contains("systemImage: micSystemImage"))
        XCTAssertTrue(source.contains("systemImage: \"arrow.up\""))
        XCTAssertTrue(source.contains("guard hasText else { return }"))
        XCTAssertTrue(source.contains(".frame(minWidth: 0, maxWidth: .infinity)"))
        XCTAssertTrue(source.contains("CompressibleTextScrollView"))
        XCTAssertTrue(source.contains("NSView.noIntrinsicMetric"))
        XCTAssertTrue(source.contains("setContentCompressionResistancePriority(.defaultLow, for: .horizontal)"))
        XCTAssertFalse(source.contains("inputComposerHeight"))
        XCTAssertFalse(source.contains(".disabled(!hasText && !isRecording)"))
    }
}
