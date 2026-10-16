#if TRANSLATION_ACCEPTANCE
import AppKit
import SwiftUI

@main struct TranslationRenderAcceptance {
    @MainActor static func main() throws {
        _ = NSApplication.shared
        let output = URL(fileURLWithPath: CommandLine.arguments[1])
        let suite = "vox.translation.render." + UUID().uuidString
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let settings = TranslationSettings(defaults: defaults)
        settings.setEnabled(true)
        let store = MessageTranslationStore(service: MessageTranslationService(snapshot: {
            fatalError("render must not request credentials")
        }, completion: { _, _ in fatalError("render must not contact provider") }), settings: settings)
        store.setVisible(true)
        let source = TranslationSource(kind: .local, id: "fixture", scope: "synthetic", text: "I want have a test about coding group", allowed: true)
        let message = VoiceMessage(id: "fixture", text: source.text, agent: "fixture", createdAt: Date(timeIntervalSince1970: 0), durationSeconds: 0, latencyMs: 0, asrProvider: nil, isMe: true)
        let sample = "我想对编程群做一个测试。"
        let states: [(String, MessageTranslationStore.State)] = [
            ("success", .result(sample, expanded: true, identical: false)),
            ("loading", .running), ("failed", .failure(.network)),
            ("unconfigured", .failure(.configuration)), ("code-only", .onlyCode),
            ("long", .result(String(repeating: "如果所有检查均通过且审批未撤销，才可以部署。保留 `count == 2` 和 https://example.test/path 。\n", count: 8), expanded: true, identical: false))
        ]
        for (name, state) in states {
            for dark in [false, true] {
                NSApp.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
                for width: CGFloat in [480, 800] {
                    let block = MessageTranslationBlock(state: state, copy: {}, collapse: {}, cancel: {}, retry: {}, settings: {})
                    let actions = MessageTextActions(store: store, current: { source }, showsOriginalCopy: false)
                    let view = VStack(alignment: .leading, spacing: TF.spacingLG) {
                        Text("Vox · 合成界面验收 / " + name).font(.caption).foregroundStyle(.secondary)
                        ChatArea(agent: "fixture", message: message, agents: [],
                            translationContent: AnyView(block), translationActions: AnyView(actions))
                        AgentTimelineTurn(message: AgentChatMessage(taskId: "fixture", agentId: "fixture", type: .text,
                            content: "Do not restart the server.", seq: 0, timestamp: Date(timeIntervalSince1970: 0)),
                            agentName: "Code Master",
                            translationContent: AnyView(MessageTranslationBlock(state: .result("不要重启服务器。", expanded: true, identical: false), copy: {}, collapse: {}, cancel: {}, retry: {}, settings: {})),
                            translationActions: AnyView(MessageTextActions(store: store, current: { source })))
                    }
                    .padding(TF.spacingLG).frame(width: width, alignment: .leading)
                    .background(TF.appBackground).environment(\.colorScheme, dark ? .dark : .light)
                    let renderer = ImageRenderer(content: view)
                    renderer.scale = 2
                    guard let image = renderer.cgImage else { fatalError("native SwiftUI rendering failed") }
                    let bitmap = NSBitmapImageRep(cgImage: image)
                    guard let data = bitmap.representation(using: .png, properties: [:]) else { fatalError("PNG encoding failed") }
                    try data.write(to: output.appendingPathComponent("\(name)-\(dark ? "dark" : "light")-\(Int(width)).png"))
                }
            }
        }
        print("PASS: 24 native SwiftUI renders; synthetic source/results only; no engine, Keychain, model or Feishu calls")
    }
}
#endif
