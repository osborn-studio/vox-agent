import SwiftUI

struct LoginView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.openWindow) private var openWindow
    @Environment(\.dismissWindow) private var dismissWindow
    @AppStorage("tf_language") private var language = AppLanguage.systemDefault

    var body: some View {
        VStack(alignment: .leading, spacing: TF.spacingLG) {
            HStack {
                Text("Vox Agent").font(.headline)
                Spacer()
                LanguageSwitcher()
            }
            Text(L("使用飞书登录你的工作空间", "Sign in to your workspace with Feishu"))
                .font(.title2).bold()
            FeishuAccountView(onSignedIn: enterWorkspace)
            Divider()
            Button(L("仅使用本机（不绑定飞书）", "Use locally without linking Feishu")) {
                LoginSession.completeLocalTeamLogin()
                enterWorkspace()
            }
            .buttonStyle(.bordered)
            Text(L("本机模式保留原有历史和配置，不会创建飞书账户绑定。", "Local mode keeps your history and configuration without linking a Feishu account."))
                .font(.caption).foregroundStyle(.secondary)
            Button(L("打开设置向导", "Open setup wizard")) { openWindow(id: "setup") }
                .buttonStyle(.plain).foregroundStyle(.secondary)
        }
        .padding(TF.spacingXL)
        .frame(width: 620, height: 540)
        .background(TF.appBackground)
        .id(language)
    }

    private func enterWorkspace() {
        openWindow(id: appState.hasCompletedSetup ? "chat" : "setup")
        dismissWindow(id: "login")
        NSApp.activate(ignoringOtherApps: true)
    }
}
