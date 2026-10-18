import XCTest

final class ChatInputBarSourceTests: XCTestCase {
    private func source() throws -> String {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Chat/ChatInputBar.swift")
        return try String(contentsOf: sourceURL, encoding: .utf8)
    }

    func testComposerUsesRecognitionSessionAsItsOnlyAudioOwner() throws {
        let source = try source()

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
        let source = try source()

        XCTAssertTrue(source.contains("SubmitAwareTextView"))
        XCTAssertTrue(source.contains("event.keyCode == 36 || event.keyCode == 76"))
        XCTAssertTrue(source.contains("!event.modifierFlags.contains(.shift)"))
        XCTAssertTrue(source.contains("onSubmit?()"))
        XCTAssertTrue(source.contains("event.keyCode == 53"))
        XCTAssertTrue(source.contains("onEscape?()"))
        XCTAssertTrue(source.contains("super.keyDown(with: event)"))
    }

    /// The text area grows upward with its content and stops at the shared
    /// maximum; the control row keeps mic and send inside the surface.
    func testComposerGrowsWithContentAndKeepsControlsInsideInput() throws {
        let source = try source()

        XCTAssertTrue(source.contains("onContentHeightChange"))
        XCTAssertTrue(source.contains("static func contentHeight(of textView: NSTextView) -> CGFloat"))
        XCTAssertTrue(source.contains("min(max(measuredTextHeight, ChatLayoutMetrics.inputTextMinHeight), ChatLayoutMetrics.inputMaxHeight)"))
        XCTAssertTrue(source.contains("ChatLayoutMetrics.inputToolbarHeight"))
        XCTAssertTrue(source.contains("scrollView.hasVerticalScroller = true"))
        XCTAssertTrue(source.contains("VoiceWaveform("))
        XCTAssertTrue(source.contains("VoiceCountdownRing("))
        XCTAssertTrue(source.contains("accessibilityReduceMotion"))
        XCTAssertTrue(source.contains("TF.recording"))
        XCTAssertTrue(source.contains("TF.amber"))
        XCTAssertTrue(source.contains("VoiceInputStateMachine"))
        XCTAssertTrue(source.contains("GeometryReader { geometry in"))
        XCTAssertTrue(source.contains("composerBody(width: max(0, geometry.size.width - ChatLayoutMetrics.inputHorizontalInset * 2))"))
        XCTAssertTrue(source.contains(".frame(width: width)"))
        XCTAssertTrue(source.contains(".fixedSize()"))
        XCTAssertTrue(source.contains(".layoutPriority(10)"))
        XCTAssertTrue(source.contains(".padding(.trailing, ChatLayoutMetrics.inputToolbarTrailingInset)"))
        XCTAssertTrue(source.contains(".frame(height: ChatLayoutMetrics.inputChipHeight)"))
        XCTAssertTrue(source.contains(".frame(maxWidth: ChatLayoutMetrics.inputModelBadgeMaxWidth)"))
        XCTAssertTrue(source.contains(".frame(width: ChatLayoutMetrics.inputIconButtonSize, height: ChatLayoutMetrics.inputIconButtonSize)"))
        XCTAssertTrue(source.contains(".frame(width: ChatLayoutMetrics.voiceCountdownRingSize, height: ChatLayoutMetrics.voiceCountdownRingSize)"))
        XCTAssertTrue(source.contains("width: ChatLayoutMetrics.inputTextInsetHorizontal,\n            height: ChatLayoutMetrics.inputTextInsetVertical"))
        XCTAssertTrue(source.contains("composerIconButton("))
        XCTAssertTrue(source.contains("systemImage: micSystemImage"))
        XCTAssertTrue(source.contains("systemImage: \"arrow.up\""))
        XCTAssertTrue(source.contains("guard hasText else { return }"))
        XCTAssertTrue(source.contains(".frame(minWidth: 0, maxWidth: .infinity)"))
        XCTAssertTrue(source.contains("CompressibleTextScrollView"))
        XCTAssertTrue(source.contains("NSView.noIntrinsicMetric"))
        XCTAssertTrue(source.contains("setContentCompressionResistancePriority(.defaultLow, for: .horizontal)"))
        XCTAssertFalse(source.contains("inputComposerHeight"))
        XCTAssertFalse(source.contains("inputButtonReserveWidth"))
        // Composer geometry comes from ChatLayoutMetrics and TF, not literals.
        XCTAssertFalse(source.contains(".frame(height: 26)"))
        XCTAssertFalse(source.contains(".frame(width: 30, height: 30)"))
        XCTAssertFalse(source.contains(".frame(width: 36, height: 36)"))
        XCTAssertFalse(source.contains(".frame(maxWidth: 220)"))
        XCTAssertFalse(source.contains(".padding(.trailing, 10)"))
        XCTAssertFalse(source.contains(".padding(.vertical, 10)"))
        XCTAssertFalse(source.contains("NSSize(width: 10, height: 8)"))
    }

    /// Translate shares the Polish provider/model, keeps the original for
    /// restore, and marks the draft so sending skips Polish.
    func testComposerTranslateSharesPolishModelAndSkipsPolishAfterwards() throws {
        let source = try source()

        XCTAssertTrue(source.contains("ComposerTranslator.translate(original)"))
        XCTAssertTrue(source.contains("ComposerTranslationDirection.target(for: textInput)"))
        XCTAssertTrue(source.contains("translationOriginal = original"))
        XCTAssertTrue(source.contains("KeychainService.selectedLLMProvider"))
        XCTAssertTrue(source.contains("KeychainService.llmDisplayConfiguration(for: provider)"))
        XCTAssertTrue(source.contains("KeychainService.llmModelConfigured(for: provider)"))
        XCTAssertTrue(source.contains("TranslationSettings.shared.generation"))
        XCTAssertTrue(source.contains(".navigateToModels"))
        XCTAssertFalse(source.contains("DoubaoChatClient(provider:").description.isEmpty)
        // The translation result is applied once and never re-polished.
        let translated = source.range(of: "textInput = result.text")
        XCTAssertNotNil(translated)
        XCTAssertTrue(source[translated!.upperBound...].hasPrefix("\n                hasPolished = true"))
    }

    /// A Polish failure never sends silently: the draft stays, the reason is
    /// shown inline, and only the explicit action sends the original.
    func testPolishFailureKeepsDraftAndNeverSendsSilently() throws {
        let source = try source()

        XCTAssertTrue(source.contains("case .failed(let reason):\n                polishFailure = reason"))
        XCTAssertTrue(source.contains("return .failed(Self.polishFailureMessage(error))"))
        XCTAssertTrue(source.contains("private func sendUnpolished()"))
        XCTAssertFalse(source.contains("} catch {\n            return await MainActor.run { !Task.isCancelled"))
        // Escape from a finalized voice turn restores unpolished text.
        let escape = source.range(of: "textInput = voiceInputPrefix")
        XCTAssertNotNil(escape)
        XCTAssertTrue(source[escape!.upperBound...].contains("hasPolished = false"))
    }
}
