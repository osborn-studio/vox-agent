import SwiftUI

func L(_ zh: String, _ en: String) -> String { zh }

@main struct LocalASRFixture: App {
    var body: some Scene {
        WindowGroup("Vox GUI Acceptance — Synthetic") { FixtureContent() }
    }
}
struct FixtureContent: View {
    @State private var scenario = "checking"
    @State private var running = true
    @State private var action = "尚无操作"
    @State private var dark = false
    var body: some View {
        VStack(alignment: .leading, spacing: TF.spacingLG) {
            Text("隔离验收：无账号、无网络调用、无真实模型")
            Picker("测试状态", selection: $scenario) {
                Text("校验中").tag("checking")
                Text("已校验").tag("verified")
                Text("旧模型").tag("legacy")
                Text("失败").tag("failed")
                Text("未安装").tag("missing")
            }.pickerStyle(.segmented)
            Toggle("深色窗口（设置内容保持浅色）", isOn: $dark)
            LocalASRStatusView(checking: scenario == "checking",
                available: scenario == "verified" || scenario == "legacy",
                legacy: scenario == "legacy", failed: scenario == "failed",
                running: running, busy: false, startFailed: scenario == "failed",
                recheck: { action = "重新检查已触发"; scenario = "checking" },
                start: { running = true; action = "启动已触发" },
                stop: { running = false; action = "停止已触发" })
            Text(action).accessibilityIdentifier("fixture.action")
        }
        .padding(TF.spacingXL)
        .frame(width: 620, height: 460, alignment: .topLeading)
        .background(TF.settingsBg)
        // Match SettingsView: its warm settings palette is intentionally light-only.
        .environment(\.colorScheme, .light)
        .preferredColorScheme(dark ? .dark : .light)
    }
}
