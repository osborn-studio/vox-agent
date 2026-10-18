import AppKit
import SwiftUI

struct FeishuTimeline: View {
    @Bindable var model: FeishuTimelineModel
    let account: FeishuAccountClient
    let conversation: String

    private var modeLabel: String {
        switch model.mode {
        case "shared_policy": L("Vox 共享政策授权的群消息", "Group messages shared under Vox policy")
        case "personal": L("个人频道镜像 · 不提供 AI 回答", "Personal mirror · no AI replies")
        default: L("本人经 Vox 提交的原始记录，并非飞书当前消息", "Your original Vox submissions, not current Feishu messages")
        }
    }
    private func author(_ item: FeishuReadItem) -> String {
        if item.authorKind == "vox" { return (item.authorName ?? L("Vox 用户", "Vox user")) + L("（经 Vox）", " (via Vox)") }
        return L("飞书发送者", "Feishu sender") + (item.authorId.isEmpty ? "" : " · " + item.authorId)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: TF.spacingSM) {
            Label(modeLabel, systemImage: "text.alignleft").font(.caption).foregroundStyle(.secondary)
            Text(L("仅显示文字；富文本、引用和话题为占位，不展开。旧消息的编辑和撤回在重新进入或加载该页时核对。", "Text only; rich messages, quotes and threads remain placeholders. Older edits and withdrawals are checked on re-entry or page load."))
                .font(.caption).foregroundStyle(.secondary)
            if model.failure != nil {
                Label(L("暂时无法读取，已清空旧内容。请重试。", "Cannot read messages; previous content cleared. Retry."), systemImage: "exclamationmark.circle")
                    .font(.caption)
            }
            if model.historyRestarted {
                Label(L("新消息较多，较早消息需重新加载。", "Many new messages arrived. Load earlier messages again."), systemImage: "info.circle").font(.caption)
            }
            if model.limitReached {
                Label(L("已达本次读取上限，请重新进入会话。", "Reading limit reached. Reopen this conversation."), systemImage: "info.circle").font(.caption)
            }
            HStack {
                Button(L("刷新", "Refresh")) { Task { await model.refresh(using: account) } }.disabled(model.loading)
                if model.nextCursor != nil {
                    Button(L("加载更早消息", "Load older messages")) { Task { await model.refresh(using: account, older: true) } }
                        .disabled(model.loading || model.limitReached)
                }
                if model.loading { Label(L("读取中", "Loading"), systemImage: "clock").font(.caption) }
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: TF.spacingLG) {
                    ForEach(model.items) { item in
                        HStack(alignment: .top, spacing: TF.spacingSM) {
                            Rectangle().fill(TF.accent).frame(width: TF.spacingXS / 2)
                            VStack(alignment: .leading, spacing: TF.spacingXS) {
                                HStack {
                                    Text(author(item)).font(.caption).foregroundStyle(.secondary)
                                    Spacer(minLength: TF.spacingSM)
                                    if !item.deleted, !item.placeholder, let text = item.text {
                                        CopyOriginalButton(text: text)
                                    }
                                }
                                if item.deleted { Label(L("消息已撤回", "Message withdrawn"), systemImage: "minus.circle") }
                                else if item.placeholder { Label(L("非纯文字内容，请在飞书查看", "Non-text content; view in Feishu"), systemImage: "doc") }
                                else { Text(item.text ?? "").textSelection(.enabled) }
                                if item.source == "submission" { Text(L("原始提交", "Original submission")).font(.caption).foregroundStyle(.secondary) }
                                else if item.updated { Text(L("已编辑", "Edited")).font(.caption).foregroundStyle(.secondary) }
                            }
                        }
                    }
                }
            }
        }
        .background(FeishuWindowVisibility(model: model, account: account, conversation: conversation).frame(width: 0, height: 0))
    }
}

/// Observe the containing chat window only, including occlusion and close. No global
/// any-window heuristic and no app activation assumption grants body visibility.
private struct FeishuWindowVisibility: NSViewRepresentable {
    let model: FeishuTimelineModel
    let account: FeishuAccountClient
    let conversation: String
    func makeNSView(context: Context) -> WindowProbe {
        WindowProbe(model: model, account: account)
    }
    func updateNSView(_ view: WindowProbe, context: Context) {
        view.select(conversation, model: model)
        view.updateVisibility()
    }
    static func dismantleNSView(_ view: WindowProbe, coordinator: ()) { view.stop() }

    @MainActor final class WindowProbe: NSView {
        let lifecycle: FeishuTimelineLifecycle
        private var observers: [NSObjectProtocol] = []
        private var closed = false
        private var selected: String?
        func select(_ conversation: String, model: FeishuTimelineModel) {
            let changed = selected != conversation || model.conversation != conversation
            selected = conversation; model.select(conversation)
            if changed { lifecycle.selectionChanged() }
        }
        init(model: FeishuTimelineModel, account: FeishuAccountClient) {
            lifecycle = FeishuTimelineLifecycle(model: model, account: account)
            super.init(frame: .zero)
        }
        required init?(coder: NSCoder) { fatalError("programmatic view only") }
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            stop(); closed = false
            guard let window else { return }
            for name in [NSWindow.didChangeOcclusionStateNotification, NSWindow.didMiniaturizeNotification,
                         NSWindow.didDeminiaturizeNotification, NSWindow.willCloseNotification] {
                observers.append(NotificationCenter.default.addObserver(forName: name, object: window, queue: .main) { [weak self] note in
                    MainActor.assumeIsolated {
                        guard let self else { return }
                        if note.name == NSWindow.willCloseNotification { self.closed = true }
                        else if self.window?.isVisible == true { self.closed = false }
                        self.updateVisibility()
                    }
                })
            }
            updateVisibility()
        }
        func updateVisibility() {
            lifecycle.visibilityChanged(!closed && window?.isVisible == true && window?.isMiniaturized == false && window?.occlusionState.contains(.visible) == true)
        }
        func stop() {
            observers.forEach { NotificationCenter.default.removeObserver($0) }; observers = []
            lifecycle.visibilityChanged(false)
        }
    }
}
