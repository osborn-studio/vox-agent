import XCTest

/// Source-level contract for the Feishu text send box's Translate affordance.
/// The outbox itself runs offline in the Feishu sending harnesses; this file
/// guards the view wiring that SwiftUI builds cannot exercise here: Translate
/// reuses `ComposerTranslator`, keeps the original for Restore, reports inline,
/// and never sends.
final class FeishuTextComposerSourceTests: XCTestCase {

    func testTranslateRewritesTheDraftThroughComposerTranslatorAndKeepsTheOriginal() throws {
        let source = try readSource("VoxLog/UI/Chat/FeishuTextComposer.swift")

        XCTAssertTrue(source.contains("ComposerTranslator.translate(original)"))
        XCTAssertTrue(source.contains("ComposerTranslationDirection.target(for: outbox.draft)"))
        XCTAssertTrue(source.contains("let original = outbox.draft"))
        XCTAssertTrue(source.contains("guard !Task.isCancelled, outbox.draft == original else { return }"))
        XCTAssertTrue(source.contains("translationOriginal = original"))
        XCTAssertTrue(source.contains("outbox.draft = result.text"))
        XCTAssertTrue(source.contains("@State private var translationOriginal: String?"))
        // Restore puts the pre-translation text back; sending or clearing forgets it.
        XCTAssertTrue(source.contains("guard let original = translationOriginal else { return }"))
        XCTAssertTrue(source.contains("outbox.draft = original"))
        XCTAssertTrue(source.contains("if outbox.draft.isEmpty || outbox.draft == translationOriginal {"))
        XCTAssertTrue(source.contains(".onChange(of: outbox.draft) {"))
        XCTAssertTrue(source.contains("translationTask?.cancel()"))
        // No Polish step and no agent-composer state leaks into the Feishu box.
        XCTAssertFalse(source.contains("hasPolished"))
        XCTAssertFalse(source.contains("polishInPlace"))
        XCTAssertFalse(source.contains("DoubaoChatClient"))
        XCTAssertFalse(source.contains("TranslationTransport."))
    }

    func testTranslateNeverSendsAndReportsInline() throws {
        let source = try readSource("VoxLog/UI/Chat/FeishuTextComposer.swift")

        let start = try XCTUnwrap(source.range(of: "    private func translateDraft() {"))
        let end = try XCTUnwrap(source.range(of: "    private func cancelTranslation() {"))
        let translate = source[start.lowerBound..<end.lowerBound]
        XCTAssertFalse(translate.contains("enqueue"))
        XCTAssertFalse(translate.contains("flush("))
        XCTAssertFalse(translate.contains("account"))
        // Only the explicit send action enqueues, and it waits for translation to finish.
        XCTAssertEqual(source.components(separatedBy: "outbox.enqueue()").count, 2)
        XCTAssertTrue(source.contains(".disabled(!outbox.canSend || isTranslating)"))
        XCTAssertTrue(source.contains("Button(L(\"发送文字\", \"Send text\"))"))
        // Progress, cancellation and failure render as inline caption text.
        XCTAssertTrue(source.contains("ComposerTranslateControls("))
        XCTAssertTrue(source.contains("if let translationNote {"))
        XCTAssertTrue(source.contains("translationNote = TranslationFailure.cancelled.message"))
        XCTAssertTrue(source.contains("(error as? TranslationFailure)?.message ?? TranslationFailure.network.message"))
        XCTAssertTrue(source.contains("L(\"译文与原文相同，未改动\", \"Translation matches the original; nothing changed\")"))
        XCTAssertTrue(source.contains("点击“发送文字”后才会发出"))
        XCTAssertTrue(source.contains(".disabled(isTranslating)"))
        XCTAssertFalse(source.contains(".alert("))
        XCTAssertFalse(source.contains(".sheet("))
        XCTAssertFalse(source.contains("withAnimation"))
    }

    func testBothComposersShareTheTranslateControl() throws {
        let control = try readSource("VoxLog/UI/Chat/ComposerTranslateControls.swift")
        let agentComposer = try readSource("VoxLog/UI/Chat/ChatInputBar.swift")
        let feishuComposer = try readSource("VoxLog/UI/Chat/FeishuTextComposer.swift")

        XCTAssertTrue(control.contains("struct ComposerTranslateControls: View"))
        XCTAssertTrue(control.contains("L(\"翻译中…\", \"Translating…\") : L(\"翻译\", \"Translate\")"))
        XCTAssertTrue(control.contains("L(\"取消\", \"Cancel\")"))
        XCTAssertTrue(control.contains("L(\"还原\", \"Restore\")"))
        XCTAssertTrue(control.contains("L(\"翻译草稿\", \"Translate draft\")"))
        XCTAssertTrue(control.contains("ChatLayoutMetrics.inputChipHeight"))
        XCTAssertTrue(control.contains("TF.surface"))
        XCTAssertTrue(control.contains("TF.line"))
        // The control renders only; translation and the draft stay with the owner.
        XCTAssertFalse(control.contains("ComposerTranslator.translate("))
        XCTAssertFalse(control.contains("@State"))
        XCTAssertFalse(control.contains("@Binding"))
        // DESIGN.md: amber and recording colours are voice-exclusive; no animation off voice surfaces.
        XCTAssertFalse(control.contains("TF.amber"))
        XCTAssertFalse(control.contains("TF.recording"))
        XCTAssertFalse(control.contains("withAnimation"))

        for composer in [agentComposer, feishuComposer] {
            XCTAssertTrue(composer.contains("ComposerTranslateControls("))
            XCTAssertTrue(composer.contains("onTranslate: translateDraft"))
            XCTAssertTrue(composer.contains("onCancel: cancelTranslation"))
            XCTAssertTrue(composer.contains("onRestore: restoreTranslation"))
            XCTAssertFalse(composer.contains("systemImage: \"character.bubble\""))
        }
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
