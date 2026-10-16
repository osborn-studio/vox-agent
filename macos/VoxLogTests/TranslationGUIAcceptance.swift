#if TRANSLATION_ACCEPTANCE
import AppKit
import SwiftUI
import Observation

@MainActor @Observable final class TranslationGUIModel {
    var scenario = "success"
    var calls = 0
    let suite = "vox.translation.gui." + UUID().uuidString
    let settings: TranslationSettings
    let defaults: UserDefaults
    var store: MessageTranslationStore!
    init() {
        defaults = UserDefaults(suiteName: suite)!
        settings = TranslationSettings(defaults: defaults)
        settings.setEnabled(true)
        store = MessageTranslationStore(service: MessageTranslationService(snapshot: { [weak self] in
            guard let self, self.scenario != "configuration" else { throw TranslationFailure.configuration }
            return try TranslationConfiguration(provider: .openai, baseURL: "https://fixture.invalid/v1",
                model: "gpt-4o", credential: "synthetic-not-a-key", generation: self.settings.generation, bypassProxy: false)
        }, completion: { [weak self] _, _ in
            guard let self else { throw CancellationError() }
            self.calls += 1
            let selected = self.scenario
            try await Task.sleep(for: .seconds(selected == "slow" ? 8 : 1))
            if selected == "failure" { throw TranslationFailure.network }
            let text = selected == "long" ? String(repeating: "请保留原文，确认测试通过后再部署。\n", count: 35) : "我想对编程群做一个测试。"
            return TranslationCompletion(text: text, finishReason: "stop", complete: true)
        }), admission: TranslationAdmission(), settings: settings)
    }
    var source: TranslationSource {
        TranslationSource(kind: .local, id: "gui-synthetic", scope: "gui",
            text: scenario == "code" ? "```swift\nlet count = 2\n```" : "I want have a test about coding group", allowed: true)
    }
    func reset() { store.clear() }
    func cleanup() { store.clear(); defaults.removePersistentDomain(forName: suite) }
}

@MainActor final class TranslationGUIDelegate: NSObject, NSApplicationDelegate {
    lazy var model = TranslationGUIModel()
    func applicationWillTerminate(_ notification: Notification) { model.cleanup() }
}

@main struct TranslationGUIAcceptance: App {
    @NSApplicationDelegateAdaptor(TranslationGUIDelegate.self) private var delegate
    var body: some Scene {
        Window("Vox Translation GUI — Synthetic", id: "translation-fixture") { TranslationGUIContent(model: delegate.model) }
        Window("Polish Settings — Synthetic", id: "settings") {
            Text("合成设置入口：不读取密钥、不修改真实模型配置").padding()
        }
    }
}
struct TranslationGUIContent: View {
    @Bindable var model: TranslationGUIModel
    @State private var dark = false
    var body: some View {
        VStack(alignment: .leading, spacing: TF.spacingMD) {
            Text("Open Context Flow · 翻译组件验收").font(.headline)
            Text("合成内容 · 无飞书、无模型网络请求 · 正式消息和翻译组件").font(.caption)
            Picker("场景", selection: $model.scenario) {
                Text("成功").tag("success")
                Text("慢请求").tag("slow")
                Text("失败").tag("failure")
                Text("未配置").tag("configuration")
                Text("纯代码").tag("code")
                Text("长译文").tag("long")
            }.onChange(of: model.scenario) { _, _ in model.reset() }
            Toggle("深色模式", isOn: $dark)
            ScrollView {
                let source = model.source
                let actions = MessageTextActions(store: model.store, current: { model.source }, showsOriginalCopy: false)
                ChatArea(agent: "Open Context Flow", message: VoiceMessage(id: source.id,
                    text: source.text, agent: "gui", createdAt: Date(timeIntervalSince1970: 0),
                    durationSeconds: 0, latencyMs: 0, asrProvider: nil, isMe: true), agents: [],
                    translationContent: AnyView(actions.resultBlock), translationActions: AnyView(actions))
            }
            Text("合成翻译调用：\(model.calls)").font(.caption).monospacedDigit()
                .accessibilityIdentifier("fixture.calls")
        }
        .padding(TF.spacingLG)
        .frame(minWidth: 480, idealWidth: 720, minHeight: 460, idealHeight: 600)
        .background(TF.appBackground)
        .preferredColorScheme(dark ? .dark : .light)
        .background(TranslationWindowVisibility(store: model.store).frame(width: 0, height: 0))
    }
}
#endif
