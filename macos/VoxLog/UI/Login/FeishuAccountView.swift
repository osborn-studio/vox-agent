import SwiftUI

struct FeishuAccountView: View {
    @Environment(AppState.self) private var appState
    var onSignedIn: (() -> Void)?

    var body: some View {
        @Bindable var account = appState.feishuAccount
        GroupBox(L("飞书账户", "Feishu account")) {
            VStack(alignment: .leading, spacing: TF.spacingSM) {
                if let profile = account.profile {
                    Text(profile.name).font(.headline)
                    Text(L("已绑定飞书账户", "Linked to Feishu")).font(.caption).foregroundStyle(.secondary)
                    Button(L("退出登录", "Sign out")) { Task { await account.logout(backup: appState.inputBackup) } }
                        .disabled(account.busy)
                } else {
                    TextField(L("团队登录服务地址", "Team sign-in service URL"), text: $account.endpoint)
                        .disabled(account.busy || account.candidate != nil)
                    Text(L("首次飞书登录自动注册。仅限管理员允许的企业；历史记录保留在本机。", "Your first Feishu login creates an account. Only approved organizations can join. History stays on this Mac."))
                        .font(.caption).foregroundStyle(.secondary)
                    Text(L("团队服务应部署在云端。127.0.0.1 仅连接这台 Mac 的开发服务。", "The team service belongs in the cloud. 127.0.0.1 only reaches this Mac’s development service."))
                        .font(.caption).foregroundStyle(.secondary)
                    Text(L("只扫描这台设备打开的登录二维码，不要扫描他人转发的二维码。", "Only scan the login QR opened on this device, never a forwarded QR code."))
                        .font(.caption).foregroundStyle(.secondary)
                    if let candidate = account.candidate {
                        Text(L("确认使用：", "Continue as: ") + candidate.name).font(.headline)
                        Text(candidate.tenantKey).font(.caption).foregroundStyle(.secondary)
                        Button(L("确认登录并绑定", "Confirm sign-in and link")) {
                            Task {
                                await account.confirm(backup: appState.inputBackup)
                                if account.profile != nil { onSignedIn?() }
                            }
                        }.disabled(account.busy || account.loginBlocked)
                    } else {
                        Button(L("飞书扫码注册 / 登录", "Sign up / sign in with Feishu")) {
                            Task { await account.begin() }
                        }.disabled(account.busy || account.loginBlocked)
                    }
                    if account.busy || account.candidate != nil {
                        Button(L("取消", "Cancel")) { account.cancel() }.disabled(account.candidate != nil && account.busy)
                    }
                }
                if account.needsCredentialCleanup {
                    Button(L("重试清理登录凭据", "Retry credential cleanup")) {
                        Task { await account.logout(backup: appState.inputBackup) }
                    }.disabled(account.busy)
                }
                if !account.status.isEmpty { Text(account.status).font(.caption).foregroundStyle(.secondary) }
            }.padding(TF.spacingSM)
        }
        .task { await account.restore() }
    }
}
