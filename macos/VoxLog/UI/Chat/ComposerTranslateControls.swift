import SwiftUI

/// Quiet Translate affordance shared by the agent composer (`ChatInputBar`)
/// and the Feishu text send box (`FeishuTextComposer`): one chip button, then
/// Cancel while a translation runs or Restore once the draft was replaced.
/// The owning view keeps the draft, the pre-translation original and the
/// `ComposerTranslator` call; this view only renders and forwards the three
/// actions, so neither composer can be sent from here.
struct ComposerTranslateControls: View {
    let isTranslating: Bool
    let canTranslate: Bool
    /// A pre-translation original exists, so Restore is offered.
    let canRestore: Bool
    /// Tooltip and accessibility hint naming the detected target language.
    let help: String
    let onTranslate: () -> Void
    let onCancel: () -> Void
    let onRestore: () -> Void

    var body: some View {
        Button(action: onTranslate) {
            Label(
                isTranslating ? L("翻译中…", "Translating…") : L("翻译", "Translate"),
                systemImage: "character.bubble"
            )
            .labelStyle(.titleAndIcon)
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(canTranslate || isTranslating ? Color.primary : Color.secondary)
            .padding(.horizontal, TF.spacingSM)
            .frame(height: ChatLayoutMetrics.inputChipHeight)
            .background(RoundedRectangle(cornerRadius: TF.cornerSM).fill(TF.surface))
            .overlay(RoundedRectangle(cornerRadius: TF.cornerSM).stroke(TF.line))
        }
        .buttonStyle(.plain)
        .disabled(!canTranslate)
        .help(help)
        .accessibilityLabel(L("翻译草稿", "Translate draft"))
        .accessibilityHint(help)

        if isTranslating {
            Button(L("取消", "Cancel"), action: onCancel)
                .buttonStyle(.plain)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
        } else if canRestore {
            Button(action: onRestore) {
                Label(L("还原", "Restore"), systemImage: "arrow.uturn.backward")
                    .labelStyle(.titleAndIcon)
            }
            .buttonStyle(.plain)
            .font(.system(size: 11))
            .foregroundStyle(.secondary)
            .help(L("恢复翻译前的原文", "Restore the text before translation"))
        }
    }
}
