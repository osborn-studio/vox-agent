import SwiftUI

struct InputBackupSettingsView: View {
    @Environment(AppState.self) private var appState
    @State private var endpoint = ""
    @State private var target = ""
    @State private var token = ""
    @State private var enabled = false
    @State private var result: String?

    var body: some View {
        FeishuAccountView()
        GroupBox(L("飞书开发输入备份（预发布）", "Feishu input backup (preview)")) {
            VStack(alignment: .leading, spacing: TF.spacingSM) {
                Text(L("仅备份聊天窗口点击发送的最终文字。记录先保存到本机，再由企业服务投递飞书；不采集外部应用回复或语音草稿。", "Backs up final text submitted in this chat window. Saved locally, then sent through your enterprise service. Excludes external app replies and voice drafts."))
                    .font(.caption).foregroundStyle(.secondary)
                Toggle(L("启用备份", "Enable backup"), isOn: $enabled)
                TextField(L("企业备份服务 HTTPS 地址", "Enterprise backup HTTPS URL"), text: $endpoint)
                TextField(L("管理员配置的目标 ID", "Administrator configured target ID"), text: $target)
                SecureField(L("个人备份凭据（留空保留原凭据）", "Personal credential (blank keeps current)"), text: $token)
                    .disabled(appState.feishuAccount.profile != nil || appState.feishuAccount.loginBlocked || appState.feishuAccount.busy)
                Text(L("此处不是飞书 app secret 或扫码 token。可在上方使用飞书登录，或使用管理员签发的个人凭据；同一飞书账号续期后待发送记录自动衔接；更换账号或服务地址时保留旧记录，需另行核对。", "Not a Feishu app secret or scan token. Sign in with Feishu above, or use an administrator-issued personal credential. Pending records continue after renewal for the same Feishu account. Changing account or server retains old records for separate reconciliation."))
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button(L("保存", "Save")) { Task { await save() } }
                        .disabled(appState.feishuAccount.loginBlocked || appState.feishuAccount.busy)
                    Button(L("立即重试", "Retry now")) {
                        Task {
                            if let configuration = appState.inputBackup.configuration {
                                await appState.inputBackup.flush(configuration: configuration)
                            }
                        }
                    }.disabled(appState.inputBackup.configuration == nil)
                }
                Text(result ?? appState.inputBackup.statusText).font(.caption)
                Text(L("本机待同步：", "Pending locally: ") + String(appState.inputBackup.pendingCount))
                    .font(.caption).monospacedDigit()
                if appState.inputBackup.pendingCount > appState.inputBackup.currentScopePendingCount {
                    Text(L("其中不匹配当前配置的记录：", "Records outside current configuration: ") + String(appState.inputBackup.pendingCount - appState.inputBackup.currentScopePendingCount))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text(L("队列满时会保留输入、暂停发送。可暂时关闭备份再发送；旧记录仍保留，管理员核对后再迁移。", "A full queue keeps the draft and stops submission. You may pause backup to send; old records remain for administrator reconciliation."))
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(TF.spacingSM)
        }
        .onChange(of: appState.feishuAccount.profile?.userId) {
            if appState.feishuAccount.profile != nil {
                loadConfiguration()
            }
        }
        .onAppear {
            loadConfiguration()
        }
    }

    private func loadConfiguration() {
        let config = appState.inputBackup.configuration
        enabled = config != nil
        endpoint = config?.endpoint.absoluteString ?? appState.feishuAccount.endpoint
        target = config?.targetID ?? InputBackupConfiguration.suggestedTarget(
            endpoint: endpoint, availableTargets: appState.feishuAccount.targetIDs)
    }

    private func save() async {
        do {
            let configuration = enabled ? try InputBackupConfiguration(endpoint: endpoint, targetID: target) : nil
            if enabled {
                if !token.isEmpty {
                    let stored = try InputBackupClient.bindCredential(token, to: configuration!.endpoint)
                    try await appState.feishuAccount.savePersonalCredential(stored)
                    token = ""
                }
                let stored = await Task.detached { KeychainService.load(key: InputBackupClient.credentialKey) }.value
                guard let stored, InputBackupClient.credentialToken(stored, for: configuration!.endpoint) != nil else {
                    throw InputBackupError.credential
                }
            }
            try appState.inputBackup.configure(configuration)
            result = nil
        } catch { result = error.localizedDescription }
    }
}
