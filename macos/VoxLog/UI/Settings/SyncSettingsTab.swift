import SwiftUI

struct SyncSettingsTab: View {

    @State private var repoURL = GitSyncEngine.repoURL
    @State private var localPath = GitSyncEngine.localPath
    @State private var isSyncing = false
    @State private var lastResult: GitSyncEngine.SyncResult?
    @State private var devices: [GitSyncEngine.DeviceInfo] = []
    @State private var notionSettings = NotionSyncSettings.load()
    @State private var notionTokenInput = ""
    @State private var notionTokenSaved = KeychainService.loadNotionToken() != nil
    @State private var isNotionSyncing = false
    @State private var notionResult: NotionSyncResult?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SettingsSectionHeader(
                label: "SYNC",
                title: L("跨设备同步", "Cross-Device Sync"),
                description: L(
                    "通过 GitHub 私有仓库在多台 Mac 之间增量同步聊天记录、Agent 配置和个人词典。每台设备只写入自己的目录，Git 仅传输变更文件。",
                    "Incrementally sync chat history, agents, and dictionary across Macs via a private GitHub repo. Each device writes only to its own folder. Git transports only changed files."
                )
            )

            ChatSubmissionSettingsView()
                .padding(.bottom, TF.spacingLG)

            InputBackupSettingsView()
                .padding(.bottom, TF.spacingLG)

            // This device
            GroupBox {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("本机", "This Device")).font(.system(size: 12, weight: .semibold))
                    SettingsRow(label: L("设备名称", "Name"), value: DeviceIdentity.deviceName)
                    SettingsRow(label: L("目录名", "Folder"), value: DeviceIdentity.shortName)
                }
                .padding(4)
            }
            .padding(.bottom, 16)

            // Repo config
            GroupBox {
                VStack(alignment: .leading, spacing: 8) {
                    Text(L("仓库配置", "Repository")).font(.system(size: 12, weight: .semibold))
                    HStack {
                        Text("URL").font(.system(size: 11)).foregroundStyle(.secondary).frame(width: 60, alignment: .leading)
                        TextField("https://github.com/...", text: $repoURL)
                            .textFieldStyle(.roundedBorder)
                            .font(.system(size: 11, design: .monospaced))
                            .onChange(of: repoURL) { GitSyncEngine.repoURL = repoURL }
                    }
                    HStack {
                        Text(L("本地路径", "Local")).font(.system(size: 11)).foregroundStyle(.secondary).frame(width: 60, alignment: .leading)
                        Text(localPath)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
                .padding(4)
            }
            .padding(.bottom, 16)

            // Sync scope
            GroupBox {
                VStack(alignment: .leading, spacing: 4) {
                    Text(L("同步范围", "Scope")).font(.system(size: 12, weight: .semibold))
                    scopeRow("bubble.left.and.bubble.right", L("聊天记录", "Chat History"), L("按天 JSONL 增量导出", "Daily JSONL incremental"))
                    scopeRow("person.2", L("Agent 配置", "Agents"), L("仅同步自建 Agent", "User-created only"))
                    scopeRow("textformat.abc", L("词典", "Dictionary"), L("片段替换 + 热词", "Snippets + hotwords"))
                }
                .padding(4)
            }
            .padding(.bottom, 16)

            // Connected devices
            if !devices.isEmpty {
                GroupBox {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L("已连接设备", "Devices")).font(.system(size: 12, weight: .semibold))
                        ForEach(devices, id: \.deviceId) { d in
                            HStack {
                                Image(systemName: "desktopcomputer")
                                    .font(.system(size: 10))
                                    .foregroundStyle(d.isSelf ? .green : .secondary)
                                Text(d.name).font(.system(size: 11))
                                if d.isSelf {
                                    Text(L("(本机)", "(this)")).font(.system(size: 9)).foregroundStyle(.green)
                                }
                                Spacer()
                                Text("\(d.totalRecords) records").font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 1)
                        }
                    }
                    .padding(4)
                }
                .padding(.bottom, 16)
            }

            // Sync button
            HStack(spacing: 12) {
                Button {
                    guard !isSyncing else { return }
                    isSyncing = true
                    Task {
                        let result = await GitSyncEngine.shared.sync()
                        await MainActor.run {
                            lastResult = result
                            isSyncing = false
                            loadDevices()
                        }
                    }
                } label: {
                    HStack(spacing: 6) {
                        if isSyncing { ProgressView().controlSize(.mini) }
                        else { Image(systemName: "arrow.triangle.2.circlepath") }
                        Text(isSyncing ? L("同步中...", "Syncing...") : L("立即同步", "Sync Now"))
                    }
                }
                .controlSize(.large)
                .buttonStyle(.borderedProminent)
                .disabled(isSyncing)

                if let r = lastResult {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(r.summary)
                            .font(.system(size: 12))
                            .foregroundStyle(r.error == nil ? .green : .red)
                        if r.duration > 0 {
                            Text(String(format: "%.1fs", r.duration))
                                .font(.system(size: 10))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            SettingsDivider()
                .padding(.vertical, 18)

            notionCloudSection
        }
        .onAppear { loadDevices() }
    }

    private var notionCloudSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text(L("Notion Cloud", "Notion Cloud"))
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(TF.settingsText)
                Text(L(
                    "把 Vox Agent 对话镜像到 Notion database，另一台 Mac 可从同一个 Notion 数据源拉取内容。",
                    "Mirror Vox Agent conversations into a Notion database so another Mac can pull from the same Notion data source."
                ))
                .font(.system(size: 11))
                .foregroundStyle(TF.settingsTextTertiary)
            }

            GroupBox {
                VStack(alignment: .leading, spacing: 10) {
                    Toggle(L("启用 Notion 同步", "Enable Notion sync"), isOn: Binding(
                        get: { notionSettings.isEnabled },
                        set: { notionSettings.isEnabled = $0; notionSettings.save() }
                    ))

                    HStack {
                        Text("Token")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .frame(width: 92, alignment: .leading)
                        SecureField(notionTokenSaved ? L("已保存在 Keychain", "Saved in Keychain") : "ntn_...", text: $notionTokenInput)
                            .textFieldStyle(.roundedBorder)
                        Button(L("保存", "Save")) {
                            saveNotionToken()
                        }
                        .disabled(notionTokenInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }

                    HStack {
                        Text(L("Data Source ID", "Data Source ID"))
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .frame(width: 92, alignment: .leading)
                        TextField("xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", text: Binding(
                            get: { notionSettings.dataSourceID },
                            set: { notionSettings.dataSourceID = $0; notionSettings.save() }
                        ))
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 11, design: .monospaced))
                    }

                    HStack {
                        Text(L("方向", "Direction"))
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .frame(width: 92, alignment: .leading)
                        Picker("", selection: Binding(
                            get: { notionSettings.direction },
                            set: { notionSettings.direction = $0; notionSettings.save() }
                        )) {
                            ForEach(NotionSyncDirection.allCases) { direction in
                                Text(direction.displayName).tag(direction)
                            }
                        }
                        .labelsHidden()
                        .pickerStyle(.segmented)
                        .frame(width: 280)
                    }

                    Toggle(L("同步原始转写文本", "Sync raw transcript text"), isOn: Binding(
                        get: { notionSettings.includeRawText },
                        set: { notionSettings.includeRawText = $0; notionSettings.save() }
                    ))

                    HStack(spacing: 12) {
                        Button {
                            syncNotionNow()
                        } label: {
                            HStack(spacing: 6) {
                                if isNotionSyncing { ProgressView().controlSize(.mini) }
                                else { Image(systemName: "cloud") }
                                Text(isNotionSyncing ? L("同步中...", "Syncing...") : L("同步 Notion", "Sync Notion"))
                            }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(isNotionSyncing || !notionSettings.isEnabled)

                        if let notionResult {
                            Text(notionResult.summary)
                                .font(.system(size: 12))
                                .foregroundStyle(notionResult.error == nil ? TF.settingsAccentGreen : TF.settingsAccentRed)
                        } else if notionTokenSaved {
                            Text(L("Token 已保存在 Keychain", "Token saved in Keychain"))
                                .font(.system(size: 11))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(4)
            }
        }
    }

    private func scopeRow(_ icon: String, _ label: String, _ detail: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "checkmark.circle.fill").font(.system(size: 10)).foregroundStyle(.green)
            Image(systemName: icon).font(.system(size: 10)).foregroundStyle(.secondary).frame(width: 14)
            Text(label).font(.system(size: 11))
            Spacer()
            Text(detail).font(.system(size: 10)).foregroundStyle(.secondary)
        }
        .padding(.vertical, 1)
    }

    private func loadDevices() {
        Task {
            let d = await GitSyncEngine.shared.listDevices()
            await MainActor.run { devices = d }
        }
    }

    private func saveNotionToken() {
        do {
            try KeychainService.saveNotionToken(notionTokenInput)
            notionTokenInput = ""
            notionTokenSaved = true
            notionResult = NotionSyncResult(uploaded: 0, downloaded: 0, skipped: 0, error: nil)
        } catch {
            notionResult = NotionSyncResult(uploaded: 0, downloaded: 0, skipped: 0, error: error.localizedDescription)
        }
    }

    private func syncNotionNow() {
        guard !isNotionSyncing else { return }
        notionSettings.save()
        isNotionSyncing = true
        Task {
            let result = await NotionSyncEngine.shared.sync()
            await MainActor.run {
                notionResult = result
                isNotionSyncing = false
            }
        }
    }
}
