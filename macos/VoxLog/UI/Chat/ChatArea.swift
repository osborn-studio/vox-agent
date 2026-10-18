import SwiftUI

// MARK: - Chat Message

// MARK: - Chat Area View

struct ChatArea: View {

    let agent: String
    let message: VoiceMessage
    let agents: [Agent]
    var onForward: (VoiceMessage, String) -> Void = { _, _ in }
    var onCreateTask: (VoiceMessage, String) -> Void = { _, _ in }
    var onRecall: (VoiceMessage) -> Void = { _ in }

    @State private var copiedId: String?

    var body: some View {
        HStack(alignment: .top, spacing: TF.spacingMD) {
            if message.isMe {
                Rectangle().fill(TF.accent).frame(width: 2)
                    .accessibilityHidden(true)
            }

            VStack(alignment: .leading, spacing: TF.spacingSM) {
                turnHeader

                if let from = message.forwardedFrom,
                   let fromAgent = agents.first(where: { $0.id == from }) {
                    Label(
                        L("转发自 \(fromAgent.name)", "Forwarded from \(fromAgent.name)"),
                        systemImage: "arrowshape.turn.up.left"
                    )
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
                }

                Text(message.text)
                    .font(.system(size: ChatLayoutMetrics.messageFontSize))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, TF.spacingMD)
        .padding(.vertical, TF.spacingXS)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contextMenu { turnContextMenu }
        .accessibilityElement(children: .contain)
    }

    private var turnHeader: some View {
        HStack(spacing: TF.spacingSM) {
            Image(systemName: "waveform")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(message.isMe ? TF.amber : TF.idle)
                .accessibilityHidden(true)

            Text(message.isMe ? L("你", "YOU") : agent.uppercased())
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .foregroundStyle(.secondary)

            Text(message.timeString)
                .font(.system(size: ChatLayoutMetrics.metadataFontSize, design: .monospaced))
                .foregroundStyle(.secondary)

            if message.durationSeconds > 0 {
                Text("\(String(format: "%.1f", message.durationSeconds))s")
                    .font(.system(size: ChatLayoutMetrics.metadataFontSize, design: .monospaced))
                    .foregroundStyle(.secondary)
            }

            if let provider = message.asrProvider {
                Text(provider.uppercased())
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(.tertiary)
            }

            Spacer(minLength: TF.spacingSM)

            Button {
                copyMessage()
            } label: {
                Image(systemName: copiedId == message.id ? "checkmark" : "doc.on.doc")
                    .font(.system(size: 10))
                    .foregroundStyle(copiedId == message.id ? TF.success : .secondary)
            }
            .buttonStyle(.plain)
            .help(L("复制全文", "Copy full text"))
            .accessibilityLabel(L("复制原文", "Copy original"))
            .accessibilityIdentifier("message.copyOriginal")
        }
    }

    @ViewBuilder
    private var turnContextMenu: some View {
        Button {
            copyMessage()
        } label: {
            Label(L("复制", "Copy"), systemImage: "doc.on.doc")
        }

        Divider()

        Menu {
            ForEach(agents.filter { $0.parentId == nil && $0.id != agent }) { target in
                Button("\(target.emoji) \(target.name)") {
                    onForward(message, target.id)
                }
            }
        } label: {
            Label(L("转发给…", "Forward to…"), systemImage: "arrowshape.turn.up.right")
        }

        Menu {
            ForEach(agents.filter { $0.parentId == nil }) { target in
                Button("\(target.emoji) \(target.name)") {
                    onCreateTask(message, target.id)
                }
            }
        } label: {
            Label(L("创建任务…", "Create task…"), systemImage: "checklist")
        }

        if message.canRecall {
            Divider()
            Button(role: .destructive) {
                onRecall(message)
            } label: {
                Label(L("撤回", "Recall"), systemImage: "arrow.uturn.backward")
            }
        }
    }

    private func copyMessage() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(message.text, forType: .string)
        copiedId = message.id
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            if copiedId == message.id {
                copiedId = nil
            }
        }
    }
}

struct ConversationEmptyState: View {
    var body: some View {
        VStack(spacing: TF.spacingMD) {
            Image(systemName: "waveform.and.mic")
                .font(.system(size: 30, weight: .light))
                .foregroundStyle(TF.amber)
            Text(L("给 Agent 一个目标", "Give your Agent an objective"))
                .font(.system(size: 17, weight: .semibold))
            Text(L(
                "键入任务，或用语音启动一段可审计的执行。",
                "Type a task or use voice to start an auditable run."
            ))
            .font(.system(size: 13))
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
        }
        .padding(.vertical, 64)
        .frame(maxWidth: .infinity)
    }
}
