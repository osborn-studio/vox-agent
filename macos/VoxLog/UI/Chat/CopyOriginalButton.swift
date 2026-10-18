import AppKit
import SwiftUI

/// Hover-reveal copy action for a timeline message. It copies the visible
/// original text only; translation lives in the composer (`ComposerTranslator`).
struct CopyOriginalButton: View {
    let text: String
    @State private var copied = false

    var body: some View {
        Button {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
            copied = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
        } label: {
            Image(systemName: copied ? "checkmark" : "doc.on.doc")
                .font(.system(size: 10))
                .foregroundStyle(copied ? TF.success : .secondary)
        }
        .buttonStyle(.plain)
        .help(L("复制原文", "Copy original"))
        .accessibilityLabel(L("复制原文", "Copy original"))
        .accessibilityIdentifier("message.copyOriginal")
    }
}
