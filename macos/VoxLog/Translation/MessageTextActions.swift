import SwiftUI
import AppKit

/// Hover actions for a timeline message. Translation moved to the composer
/// (see `ComposerTranslator`); this surface only offers copying the original,
/// so copying never picks up a translated rendering.
struct MessageTextActions: View {
    let store: MessageTranslationStore
    let current: () -> TranslationSource?
    var resultOnly = false
    var showsOriginalCopy = true

    /// Kept for call-site compatibility: the timeline no longer renders a
    /// translation result block, so this view is empty.
    var resultBlock: Self { var copy = self; copy.resultOnly = true; return copy }

    private func copy(_ text: String) {
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string)
    }
    var body: some View {
        if !resultOnly, showsOriginalCopy, current() != nil {
            HStack(spacing: TF.spacingMD) {
                Button { store.copyOriginal(current: current, copy: copy) } label: {
                    Label(L("复制原文", "Copy original"), systemImage: "doc.on.doc")
                }
            }
            .buttonStyle(.plain).font(.caption).labelStyle(.iconOnly)
            .contextMenu {
                Button(L("复制原文", "Copy original")) { store.copyOriginal(current: current, copy: copy) }
            }
            .accessibilityElement(children: .contain)
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
