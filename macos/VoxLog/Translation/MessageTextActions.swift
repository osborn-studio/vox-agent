import SwiftUI
import AppKit

/// Only copy and translate capabilities are passed into this component.
struct MessageTextActions: View {
    let store: MessageTranslationStore
    let current: () -> TranslationSource?
    var resultOnly = false
    var showsOriginalCopy = true
    @Environment(\.openWindow) private var openWindow

    var resultBlock: Self { var copy = self; copy.resultOnly = true; return copy }

    private func copy(_ text: String) {
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string)
    }
    var body: some View {
        if let source = current() {
            VStack(alignment: .leading, spacing: TF.spacingSM) {
                if !resultOnly {
                    HStack(spacing: TF.spacingMD) {
                        if showsOriginalCopy {
                            Button { store.copyOriginal(current: current, copy: copy) } label: {
                                Label(L("复制原文", "Copy original"), systemImage: "doc.on.doc")
                            }
                        }
                        if store.enabled {
                            Button { store.translate(current: current) } label: {
                                Label(L("翻译为中文", "Translate to Chinese"), systemImage: "character.bubble")
                            }
                            .disabled(!source.allowed || store.state(source.key) == .running)
                            .help(source.allowed ? translationHelp : TranslationFailure.denied.message)

                        }
                    }
                    .buttonStyle(.plain).font(.caption).labelStyle(.iconOnly)
                    .contextMenu {
                        Button(L("复制原文", "Copy original")) { store.copyOriginal(current: current, copy: copy) }
                        if store.enabled && source.allowed {
                            Button(L("翻译为中文", "Translate to Chinese")) { store.translate(current: current) }
                            Button(L("重新翻译", "Translate again")) { store.translate(current: current, retry: true) }
                        }
                    }
                }
                if resultOnly, store.enabled, let state = store.state(source.key) {
                    MessageTranslationBlock(state: state,
                        copy: { store.copyResult(source.key, copy: copy) },
                        collapse: { store.collapse(source.key) },
                        cancel: { store.cancel(source.key) },
                        retry: { store.translate(current: current, retry: true) },
                        settings: { openWindow(id: "settings") })
                }
            }
            .accessibilityElement(children: .contain)
        }
    }
    private var translationHelp: String {
        _ = TranslationSettings.shared.generation
        let provider = KeychainService.selectedLLMProvider
        let name = provider == .ollama ? "Ollama" : provider.displayName
        let summary = KeychainService.llmDisplayConfiguration(for: provider)
        return L("将本条文字交由 \(name)/\(summary.model) 翻译，不发送至飞书", "Send this text to \(name)/\(summary.model) for translation; no Feishu send")
    }
}

struct MessageTranslationBlock: View {
    let state: MessageTranslationStore.State
    let copy: () -> Void
    let collapse: () -> Void
    let cancel: () -> Void
    let retry: () -> Void
    let settings: () -> Void
    var body: some View {
        switch state {
        case .running:
            HStack(spacing: TF.spacingSM) {
                Label(L("翻译中…", "Translating…"), systemImage: "clock")
                Button(L("取消", "Cancel"), action: cancel)
            }.font(.caption)
        case .result(let text, let expanded, let identical):
            if expanded {
                VStack(alignment: .leading, spacing: TF.spacingSM) {
                    HStack(spacing: TF.spacingMD) {
                        Text(L("中文译文", "Chinese translation")).foregroundStyle(.secondary)
                        Spacer(minLength: TF.spacingSM)
                        Button(L("复制译文", "Copy translation"), action: copy)
                        Button(L("收起", "Collapse"), action: collapse)
                    }.font(.caption).buttonStyle(.plain)
                    if identical { Text(L("结果与原文相同", "Result matches the original")).font(.caption).foregroundStyle(.secondary) }
                    Text(verbatim: text).font(.body).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
                .padding(TF.spacingSM)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(TF.surface, in: RoundedRectangle(cornerRadius: TF.cornerSM))
                .overlay(RoundedRectangle(cornerRadius: TF.cornerSM).stroke(TF.line))
            }
        case .failure(let failure):
            HStack(spacing: TF.spacingSM) {
                Label(failure.message, systemImage: "exclamationmark.circle")
                if failure == .configuration || failure == .unsupported {
                    Button(L("设置", "Settings"), action: settings)
                } else if ![.inputLimit, .incomplete, .outputLimit, .denied, .syntax].contains(failure) {
                    Button(L("重试", "Retry"), action: retry)
                }
            }.font(.caption)
        case .onlyCode:
            Label(L("纯代码，无需翻译。", "Code only; no translation needed."), systemImage: "chevron.left.forwardslash.chevron.right").font(.caption)
        }
    }
}

/// The actual containing window, also used for local messages. No key lookup or
/// network work is performed when the view appears.
struct TranslationWindowVisibility: NSViewRepresentable {
    let store: MessageTranslationStore
    func makeNSView(context: Context) -> Probe { Probe(store) }
    func updateNSView(_ view: Probe, context: Context) { view.updateVisibility() }
    static func dismantleNSView(_ view: Probe, coordinator: ()) { view.stop() }
    @MainActor final class Probe: NSView {
        let store: MessageTranslationStore
        var observers: [NSObjectProtocol] = []
        var closed = false
        init(_ store: MessageTranslationStore) { self.store = store; super.init(frame: .zero) }
        required init?(coder: NSCoder) { fatalError("programmatic view only") }
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow(); stop(); closed = false
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
            store.setVisible(!closed && window?.isVisible == true && window?.isMiniaturized == false && window?.occlusionState.contains(.visible) == true)
        }
        func stop() {
            observers.forEach(NotificationCenter.default.removeObserver); observers = []; store.setVisible(false)
        }
    }
}
