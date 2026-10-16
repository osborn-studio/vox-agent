import SwiftUI
import AppKit

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// MARK: - ASR Settings Card
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

struct ASRSettingsCard: View, SettingsCardHelpers {

    @State private var selectedASRProvider: ASRProvider = .volcano
    @State private var asrCredentialValues: [String: String] = [:]
    @State private var savedASRValues: [String: String] = [:]
    @State private var editedFields: Set<String> = []
    @State private var asrTestStatus: SettingsTestStatus = .idle
    @State private var isEditingASR = true
    @State private var hasStoredASR = false
    @State private var testTask: Task<Void, Never>?
    /// Hint shown below ASR credentials when only bigasr works (not seed 2.0)
    @State private var volcResourceHint: String?

    // Local model states
    @State private var localModelAvailable = false
    @State private var modelChecking = true
    @ObservedObject private var componentJob = ModelComponentJob.shared
    @State private var modelMirror = ""
    private var modelImporting: Bool { componentJob.busy }
    private var modelDownloading: Bool { componentJob.downloading }
    private var modelImportStatus: String { get { componentJob.status } nonmutating set { componentJob.status = newValue } }
    @State private var modelCheckFailed = false
    @State private var legacyModelUnverified = false
    @State private var modelStatusTask: Task<Void, Never>?
    @State private var qwen3Running = false
    @State private var qwen3Toggling = false
    @State private var qwen3StartFailed = false
    @AppStorage("tf_qwen3FinalEnabled") private var qwen3FinalEnabled = true

    // Model refresh
    @State private var isRefreshingASRModels = false
    @State private var refreshedASRModels: [String] = []

    private var currentASRFields: [CredentialField] {
        ASRProviderRegistry.configType(for: selectedASRProvider)?.credentialFields ?? []
    }

    private var isZeroCredentialProvider: Bool {
        currentASRFields.isEmpty && !selectedASRProvider.isLocal
    }

    /// Effective values: saved base + defaults for unsaved fields + dirty edits overlaid.
    private var effectiveASRValues: [String: String] {
        var result = savedASRValues
        // Fill in defaults for fields not yet saved (new provider scenario)
        for (key, value) in asrCredentialValues where result[key] == nil {
            result[key] = value
        }
        for key in editedFields {
            result[key] = asrCredentialValues[key] ?? ""
        }
        return result
    }

    private var hasASRCredentials: Bool {
        let required = currentASRFields.filter { !$0.isOptional }
        let effective = effectiveASRValues
        return required.allSatisfy { field in
            !(effective[field.key] ?? "").isEmpty
        }
    }

    private var isASRProviderAvailable: Bool {
        ASRProviderRegistry.entry(for: selectedASRProvider)?.isAvailable ?? false
    }

    private var currentASRGuideLinks: [(prefix: String?, label: String, url: URL)] {
        switch selectedASRProvider {
        case .volcano:
            return [
                (L("配置指南", "Setup guide"), L("查看", "view"), URL(string: "https://my.feishu.cn/wiki/QdEnwBMfUi0mN4k3ucMcNYhUnXr")!),
            ]
        case .deepgram:
            return [
                (L("可用模型", "Models"), L("查看", "view"), URL(string: "https://developers.deepgram.com/docs/models-languages-overview/")!),
                ("API Key", L("获取", "get"), URL(string: "https://developers.deepgram.com/docs/create-additional-api-keys")!),
            ]
        case .assemblyai:
            return [
                (L("可用模型", "Models"), L("查看", "view"), URL(string: "https://www.assemblyai.com/docs/getting-started/models")!),
                ("API Key", L("获取", "get"), URL(string: "https://www.assemblyai.com/docs/faq/how-to-get-your-api-key")!),
            ]
        case .elevenlabs:
            return [
                ("API Key", L("获取", "get"), URL(string: "https://elevenlabs.io/app/settings/api-keys")!),
            ]
        case .soniox:
            return [
                ("API Key", L("获取", "get"), URL(string: "https://console.soniox.com")!),
            ]
        case .bailian:
            return [
                (L("可用模型", "Models"), L("查看", "view"), URL(string: "https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api")!),
                ("API Key", L("获取", "get"), URL(string: "https://help.aliyun.com/zh/model-studio/get-api-key")!),
            ]
        default:
            return []
        }
    }

    @ViewBuilder
    private func providerMenuItem(_ provider: ASRProvider) -> some View {
        Button {
            selectedASRProvider = provider
        } label: {
            Text(provider.displayName)
        }
    }

    private var currentProviderNote: String? {
        switch selectedASRProvider {
        case .deepgram:
            return L("受接口限制，热词仅取前 30 个", "Due to API limits, only the first 30 hotwords are used")
        default:
            return nil
        }
    }

    // MARK: Body

    var body: some View {
        settingsGroupCard(L("语音识别引擎", "ASR Provider"), icon: "mic.fill") {
            if modelImporting {
                HStack {
                    Text(componentJob.cancelling ? L("正在取消…", "Cancelling…") : (modelDownloading ? L("正在下载并校验模型…", "Downloading and verifying model…") : L("正在复制并校验模型…", "Copying and verifying model…")))
                    Spacer()
                    Button(L("取消", "Cancel")) {
                        componentJob.cancel(status: L("正在取消；已发布缓存保留。", "Cancelling; published cache remains."))
                    }.disabled(componentJob.cancelling).accessibilityIdentifier("localASR.cancelImport")
                }
            }
            HStack {
                asrProviderPicker
                Spacer()
                Button {
                    refreshASRModels()
                } label: {
                    HStack(spacing: 3) {
                        if isRefreshingASRModels {
                            ProgressView().controlSize(.mini)
                        } else {
                            Image(systemName: "arrow.clockwise")
                                .font(.system(size: 10))
                        }
                        Text(L("刷新模型", "Refresh"))
                            .font(.system(size: 10))
                    }
                    .foregroundStyle(TF.settingsAccentBlue)
                }
                .buttonStyle(.plain)
                .disabled(isRefreshingASRModels)
                .help(L("从 API 获取最新 ASR 模型列表", "Fetch latest ASR models"))
            }

            // Fetched ASR models (clickable tags)
            if !refreshedASRModels.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text(L("可用 ASR 模型 (\(refreshedASRModels.count))", "Available (\(refreshedASRModels.count))"))
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(TF.settingsTextTertiary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 4) {
                            ForEach(refreshedASRModels, id: \.self) { model in
                                Button {
                                    asrCredentialValues["model"] = model
                                    editedFields.insert("model")
                                } label: {
                                    Text(model)
                                        .font(.system(size: 10))
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(RoundedRectangle(cornerRadius: 4).fill(TF.settingsCardAlt))
                                        .foregroundStyle(TF.settingsText)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
                .padding(.vertical, 4)
            }

            if !currentASRGuideLinks.isEmpty {
                HStack(spacing: 6) {
                    ForEach(Array(currentASRGuideLinks.enumerated()), id: \.offset) { index, link in
                        if index > 0 {
                            Text("·").font(.system(size: 10)).foregroundStyle(TF.settingsTextTertiary)
                        }
                        if let prefix = link.prefix {
                            Text(prefix).font(.system(size: 10)).foregroundStyle(TF.settingsTextTertiary)
                        }
                        Button {
                            NSWorkspace.shared.open(link.url)
                        } label: {
                            HStack(spacing: 2) {
                                Text(link.label)
                                Image(systemName: "arrow.up.right")
                                    .font(.system(size: 7))
                            }
                            .foregroundStyle(TF.settingsAccentBlue)
                        }
                        .buttonStyle(.plain)
                        .font(.system(size: 10, weight: .medium))
                    }
                }
                .padding(.bottom, 4)
            }
            SettingsDivider()

            if selectedASRProvider.isLocal {
                localModelSection
            } else {
                if isZeroCredentialProvider {
                    Text(L("此引擎无需 API 凭证，可直接测试和使用。", "This provider requires no API credentials and can be used directly."))
                        .font(.system(size: 11))
                        .foregroundStyle(TF.settingsTextSecondary)
                        .padding(.vertical, 8)
                } else if hasASRCredentials && !isEditingASR {
                    credentialSummaryCard(rows: asrSummaryRows)
                } else {
                    dynamicCredentialFields
                }

                HStack(spacing: 8) {
                    Spacer()
                    testButton(L("测试连接", "Test"), status: asrTestStatus) { testASRConnection() }
                        .disabled(!hasASRCredentials || !isASRProviderAvailable)
                    if isZeroCredentialProvider {
                        EmptyView()
                    } else if hasASRCredentials && !isEditingASR {
                        secondaryButton(L("修改", "Edit")) {
                            testTask?.cancel()
                            asrTestStatus = .idle
                            asrCredentialValues = [:]
                            editedFields = []
                            isEditingASR = true
                        }
                    } else {
                        if hasASRCredentials && hasStoredASR {
                            secondaryButton(L("取消", "Cancel")) {
                                testTask?.cancel()
                                asrTestStatus = .idle
                                loadASRCredentials()
                            }
                        }
                        primaryButton(L("保存", "Save")) { saveASRCredentials() }
                            .disabled(!hasASRCredentials)
                    }
                }
                .padding(.top, 12)

                if let hint = volcResourceHint {
                    Text(hint)
                        .font(.system(size: 11))
                        .foregroundStyle(TF.settingsAccentAmber)
                        .padding(.top, 4)
                }



            }
        }
        .onChange(of: componentJob.completionRevision) { _, _ in refreshModelStatus() }
        .onDisappear { modelStatusTask?.cancel() }
        .task {
            loadASRCredentials()
            refreshModelStatus()
            // Load cached ASR models
            let cached = await ASRModelListFetcher.shared.models(for: selectedASRProvider)
            if !cached.isEmpty { refreshedASRModels = cached }
        }
    }

    private func refreshASRModels() {
        isRefreshingASRModels = true
        Task {
            let catalog = await ASRModelListFetcher.shared.refreshAll()
            await MainActor.run {
                refreshedASRModels = catalog[selectedASRProvider.rawValue] ?? []
                isRefreshingASRModels = false
            }
        }
    }

    // MARK: - Provider Picker

    private static let recommendedProviders: [ASRProvider] = [.openai, .bailianUS, .bailian]
    private static let localProviders: [ASRProvider] = [.sherpa]

    private var asrProviderPicker: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L("识别引擎", "Provider").uppercased())
                .font(.system(size: 10, weight: .semibold))
                .tracking(0.8)
                .foregroundStyle(TF.settingsTextTertiary)
            HStack(spacing: 10) {
                let localSet = Set(Self.localProviders)
                let availableSet = Set(ASRProvider.allCases
                    .filter { localSet.contains($0) || (ASRProviderRegistry.entry(for: $0)?.isAvailable ?? false) })
                let recommended = Self.recommendedProviders.filter { availableSet.contains($0) }
                let local = Self.localProviders.filter { availableSet.contains($0) }
                let others = ASRProvider.allCases.filter { availableSet.contains($0) && !Self.recommendedProviders.contains($0) && !Self.localProviders.contains($0) }

                Menu {
                    if !recommended.isEmpty {
                        Section(L("推荐", "Recommended")) {
                            ForEach(recommended, id: \.rawValue) { provider in
                                providerMenuItem(provider)
                            }
                        }
                    }
                    if !local.isEmpty {
                        Section(L("本地", "Local")) {
                            ForEach(local, id: \.rawValue) { provider in
                                providerMenuItem(provider)
                            }
                        }
                    }
                    if !others.isEmpty {
                        Section(L("其他", "Others")) {
                            ForEach(others, id: \.rawValue) { provider in
                                providerMenuItem(provider)
                            }
                        }
                    }
                } label: {
                    HStack(spacing: 8) {
                        Text(selectedASRProvider.displayName)
                            .font(.system(size: 13))
                            .foregroundStyle(TF.settingsText)
                        Spacer()
                        Image(systemName: "chevron.down")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(TF.settingsTextTertiary)
                    }
                    .padding(.horizontal, 12)
                    .frame(height: 36)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(TF.settingsCardAlt)
                    )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.vertical, 6)
        .onChange(of: selectedASRProvider) { oldProvider, newProvider in
            // Skip if this is the initial load (oldProvider is the @State default, not a real switch)
            guard oldProvider == KeychainService.selectedASRProvider || oldProvider == newProvider else {
                // Initial load: just sync credentials, don't start/stop servers
                loadASRCredentialsForProvider(newProvider)
                refreshModelStatus()
                return
            }

            testTask?.cancel()
            asrTestStatus = .idle
            isEditingASR = true
            KeychainService.selectedASRProvider = newProvider
            loadASRCredentialsForProvider(newProvider)
            refreshModelStatus()
            // Stop servers when switching away from local ASR
            if oldProvider == .sherpa && newProvider != .sherpa {
                Task {
                    await SenseVoiceServerManager.shared.stopQwen3()
                    qwen3Running = false
                }
            }
            // Start Qwen3 when user explicitly switches to local ASR.
            if newProvider == .sherpa {
                qwen3FinalEnabled = true
                toggleQwen3(true)
            }
        }
    }

    // MARK: - Credential Fields

    private var dynamicCredentialFields: some View {
        let fields = currentASRFields
        let rows = stride(from: 0, to: fields.count, by: 2).map { i in
            Array(fields[i..<min(i+2, fields.count)])
        }
        return VStack(spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                if index > 0 { SettingsDivider() }
                HStack(alignment: .top, spacing: 16) {
                    ForEach(row) { field in
                        credentialFieldRow(field)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    if row.count == 1 {
                        Spacer().frame(maxWidth: .infinity)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func credentialFieldRow(_ field: CredentialField) -> some View {
        if !field.options.isEmpty {
            let pickerBinding = Binding<String>(
                get: {
                    let val = asrCredentialValues[field.key] ?? ""
                    return val.isEmpty ? (savedASRValues[field.key] ?? field.defaultValue) : val
                },
                set: {
                    asrCredentialValues[field.key] = $0
                    editedFields.insert(field.key)
                }
            )
            settingsPickerField(field.label, selection: pickerBinding, options: field.options)
        } else if field.isSecure {
            let binding = Binding<String>(
                get: { asrCredentialValues[field.key] ?? "" },
                set: {
                    asrCredentialValues[field.key] = $0
                    editedFields.insert(field.key)
                }
            )
            let savedVal = savedASRValues[field.key] ?? ""
            let placeholder = savedVal.isEmpty ? field.placeholder : maskedSecret(savedVal)
            settingsSecureField(field.label, text: binding, prompt: placeholder)
        } else {
            let binding = Binding<String>(
                get: {
                    let val = asrCredentialValues[field.key] ?? ""
                    if val.isEmpty {
                        return savedASRValues[field.key] ?? field.defaultValue
                    }
                    return val
                },
                set: {
                    asrCredentialValues[field.key] = $0
                    editedFields.insert(field.key)
                }
            )
            settingsField(field.label, text: binding, prompt: field.placeholder)
        }
    }

    private var asrSummaryRows: [(String, String)] {
        var rows: [(String, String)] = []
        for field in currentASRFields {
            let val = asrCredentialValues[field.key] ?? ""
            guard !val.isEmpty else { continue }
            let displayValue: String
            if field.isSecure {
                displayValue = maskedSecret(val)
            } else if let option = field.options.first(where: { $0.value == val }) {
                displayValue = option.label
            } else {
                displayValue = val
            }
            rows.append((field.label, displayValue))
        }
        return rows
    }

    // MARK: - Local Model Section

    private var localModelSection: some View {
        VStack(alignment: .leading, spacing: TF.spacingSM) {
            LocalASRStatusView(checking: modelChecking, available: localModelAvailable,
                legacy: legacyModelUnverified, failed: modelCheckFailed,
                running: qwen3Running, busy: qwen3Toggling, startFailed: qwen3StartFailed,
                recheck: refreshModelStatus, start: { toggleQwen3(true) }, stop: { toggleQwen3(false) })
            Button(L("从本地目录导入 Qwen 模型", "Import Qwen model from local folder")) { chooseModelImport() }
                .disabled(modelImporting || modelChecking || qwen3Toggling)
                .accessibilityIdentifier("localASR.import")
            if !modelImportStatus.isEmpty { Text(modelImportStatus).font(.caption).foregroundStyle(TF.settingsTextSecondary) }
            Text(L("选择包含 model.safetensors 的模型目录；源文件保留，不下载、不自动启动或切换识别。完整校验可能耗时。", "Choose the model folder containing model.safetensors. Source files remain; no download, automatic start or ASR switch. Full verification can take time."))
                .font(.caption).foregroundStyle(TF.settingsTextSecondary)
            TextField(L("模型镜像 HTTPS 目录（可选）", "Model mirror HTTPS directory (optional)"), text: $modelMirror)
                .disabled(modelImporting)
                .accessibilityIdentifier("localASR.modelMirror")
            Button(L("从镜像下载并安装 Qwen", "Download and install Qwen from mirror")) { downloadModel() }
                .disabled(modelImporting || modelChecking || localModelAvailable || qwen3Toggling || modelMirror.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityIdentifier("localASR.downloadModel")
            if componentJob.busy, componentJob.downloading, let progress = componentJob.downloadProgress {
                Text(L("本次接收：", "Received this attempt: ") + "\(progress.receivedBytes) " + L("字节；复用：", "bytes; reused: ") + "\(progress.reusedBytes) " + L("字节；校验通过：", "bytes; verified: ") + "\(progress.verifiedBytes)/\(progress.totalBytes) " + L("字节。接收或文件校验通过均不代表安装完成。", "bytes. Received or file-verified bytes do not mean installation is complete."))
                    .font(.caption).monospacedDigit().foregroundStyle(TF.settingsTextSecondary)
                    .accessibilityIdentifier("localASR.downloadProgress")
            }
            Text(L("约 1.9 GB，镜像目录须直接提供固定版本文件，运营者可见下载请求。完整校验后安装；关闭设置不会取消，可重新打开取消。", "About 1.9 GB; mirror directory must directly serve pinned files and its operator sees requests. Installation follows full verification. Closing settings does not cancel; reopen to cancel."))
                .font(.caption).foregroundStyle(TF.settingsTextSecondary)
            if localModelAvailable && !modelChecking {
                SettingsDivider()
                testButton(L("测试连接", "Test"), status: asrTestStatus) { testLocalModel() }
            }
        }
    }

    private func chooseModelImport() {
        guard !modelImporting else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.treatsFilePackagesAsDirectories = true
        panel.prompt = L("导入模型", "Import model")
        guard panel.runModal() == .OK, let source = panel.url, !modelImporting else { return }
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        componentJob.start(downloading: false,
            success: L("模型已导入或复用，完整性校验通过；原文件保留。", "Model imported or reused and integrity verified; source files retained."),
            failure: componentFailureMessage) {
                try OfflineModelImporter.importQwen(source: source, applicationSupport: support)
            }
    }

    private func downloadModel() {
        guard !modelImporting else { return }
        guard let mirror = URL(string: modelMirror.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            modelImportStatus = L("请输入有效的 HTTPS 镜像目录。", "Enter a valid HTTPS mirror directory.")
            return
        }
        do { try PinnedModelDownload.validateMirror(mirror) } catch {
            modelImportStatus = L("请输入不含账号、查询参数或片段的 HTTPS 镜像目录。", "Enter an HTTPS mirror directory without credentials, query or fragment.")
            return
        }
        let parent = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        componentJob.startDownload(
            success: L("下载与安装校验通过；未自动启动识别。", "Download and installation verified; ASR was not started."),
            failure: componentFailureMessage) { report in
                return try await PinnedModelDownload.qwenAndImport(mirror: mirror, parent: parent, progress: report)
            }
    }

    private func componentFailureMessage(_ error: Error) -> String {
        switch error {
        case is CancellationError:
            return L("已取消；已发布模型及完整下载检查点保留。同一地址重试会重新校验并复用完整文件；未完成文件重下。", "Cancelled; published models and complete download checkpoints remain. Retry the same mirror to reverify/reuse complete files; incomplete files restart.")
        case PinnedModelDownload.Failure.alreadyInstalled:
            return L("已存在校验通过的缓存，未下载；请重新检查。", "Verified cache already exists; no download. Recheck status.")
        case PinnedModelDownload.Failure.installedCheckpointCleanupUnconfirmed:
            return L("模型导入已完成，但下载检查点清理未确认；检查模型状态，勿删除其他目录。", "Model import completed, but checkpoint cleanup is unconfirmed. Recheck model status; do not remove other directories.")
        case VerifiedModelDownloadCache.Failure.capacity:
            return L("保留的下载任务已达上限；原文件保留，请联系管理员核对检查点。", "Retained download jobs reached the limit; original files remain. Ask an administrator to review checkpoints.")
        case OfflineModelImporter.Failure.installed:
            return L("模型可能已安装，但持久化未确认；请重新检查。", "Model may be installed but durability is unconfirmed; recheck.")
        case ModelStore.Failure.busy, OfflineModelImporter.Failure.busy, VerifiedModelDownloadCache.Failure.busy:
            return L("缓存正在被使用；稍后重试。", "Cache is busy; retry later.")
        default:
            return L("模型操作未确认；检查版本、网络、权限和磁盘空间。源文件及已发布缓存保留。", "Model operation unconfirmed; check revision, network, permissions and disk space. Original files and published cache remain.")
        }
    }

    private func refreshModelStatus() {
        modelStatusTask?.cancel()
        modelChecking = true
        modelCheckFailed = false
        modelStatusTask = Task {
            let runningPort = await SenseVoiceServerManager.shared.qwen3Port
            guard !Task.isCancelled else { return }
            qwen3Running = runningPort != nil
            do {
                let available = try await ModelManager.verifiedQwenAvailability()
                guard !Task.isCancelled else { return }
                switch available {
                case .managedVerified:
                    localModelAvailable = true
                    legacyModelUnverified = false
                case .legacyUnverified:
                    localModelAvailable = true
                    legacyModelUnverified = true
                case .unavailable:
                    localModelAvailable = false
                    legacyModelUnverified = false
                }
            } catch {
                guard !Task.isCancelled else { return }
                localModelAvailable = false
                modelCheckFailed = true
            }
            let port = await SenseVoiceServerManager.shared.qwen3Port
            guard !Task.isCancelled else { return }
            qwen3Running = port != nil
            modelChecking = false
        }
    }

    private func toggleQwen3(_ enabled: Bool) {
        qwen3FinalEnabled = enabled
        qwen3Toggling = true
        qwen3StartFailed = false
        Task {
            let mgr = SenseVoiceServerManager.shared
            if enabled {
                do {
                    try await mgr.startQwen3()
                    qwen3Running = await mgr.qwen3Port != nil
                } catch {
                    NSLog("[ASRSettings] Qwen3 start failed: %@", String(describing: error))
                    qwen3FinalEnabled = false
                    qwen3StartFailed = true
                }
            } else {
                await mgr.stopQwen3()
                qwen3Running = false
            }
            qwen3Toggling = false
        }
    }

    private func testLocalModel() {
        testTask?.cancel()
        asrTestStatus = .testing
        testTask = Task {
            let mgr = SenseVoiceServerManager.shared
            guard !Task.isCancelled else { return }

            let qwen3Healthy = await mgr.isHealthy()
            guard !Task.isCancelled else { return }

            if qwen3Healthy {
                asrTestStatus = .success
            } else {
                let q3Port = SenseVoiceServerManager.currentQwen3Port
                if q3Port == nil {
                    asrTestStatus = .failed(L("服务未启动", "No server running"))
                } else {
                    asrTestStatus = .failed(L("服务未就绪，请稍候重试", "Server not ready, try again"))
                }
            }
        }
    }

    // MARK: - Data

    private func loadASRCredentials() {
        selectedASRProvider = KeychainService.selectedASRProvider
        loadASRCredentialsForProvider(selectedASRProvider)
    }

    private func loadASRCredentialsForProvider(_ provider: ASRProvider) {
        testTask?.cancel()
        editedFields = []
        if let values = KeychainService.loadASRCredentials(for: provider) {
            asrCredentialValues = values
            savedASRValues = values
            hasStoredASR = true
            isEditingASR = !hasASRCredentials
        } else {
            var defaults: [String: String] = [:]
            let fields = ASRProviderRegistry.configType(for: provider)?.credentialFields ?? []
            for field in fields where !field.defaultValue.isEmpty {
                defaults[field.key] = field.defaultValue
            }
            asrCredentialValues = defaults
            savedASRValues = [:]
            hasStoredASR = false
            isEditingASR = true
        }
    }

    private func saveASRCredentials() {
        let values = effectiveASRValues
        do {
            try KeychainService.saveASRCredentials(for: selectedASRProvider, values: values)
            KeychainService.selectedASRProvider = selectedASRProvider
            asrCredentialValues = values
            savedASRValues = values
            editedFields = []
            hasStoredASR = true
            isEditingASR = false
            asrTestStatus = .saved
        } catch {
            asrTestStatus = .failed(L("保存失败", "Save failed"))
        }
    }

    private func testASRConnection() {
        testTask?.cancel()
        asrTestStatus = .testing
        volcResourceHint = nil
        let testValues = effectiveASRValues
        let provider = selectedASRProvider
        testTask = Task {
            // Volcengine: auto-detect when "auto" is selected
            if provider == .volcano && (testValues["resourceId"] ?? "") == VolcanoASRConfig.resourceIdAuto {
                await testVolcanoWithAutoResource(baseValues: testValues)
                return
            }
            do {
                guard let configType = ASRProviderRegistry.configType(for: provider),
                      let config = configType.init(credentials: testValues),
                      let client = ASRProviderRegistry.createClient(for: provider)
                else {
                    guard !Task.isCancelled else { return }
                    asrTestStatus = .failed(L("不支持", "Unsupported"))
                    return
                }
                try await client.connect(config: config, options: currentASRRequestOptions(enablePunc: false))
                await client.disconnect()
                guard !Task.isCancelled else { return }
                asrTestStatus = .success
            } catch {
                guard !Task.isCancelled else { return }
                asrTestStatus = .failed(Self.describeConnectionError(error))
            }
        }
    }

    /// Test both Volcengine resource IDs and pick the best one.
    /// Saves with resourceId="auto" so the picker stays on "Auto", and stores the
    /// resolved ID in "resolvedResourceId" for actual connections.
    private func testVolcanoWithAutoResource(baseValues: [String: String]) async {
        let options = currentASRRequestOptions(enablePunc: false)
        let seedId = VolcanoASRConfig.resourceIdSeedASR
        let bigId = VolcanoASRConfig.resourceIdBigASR

        // Test Seed ASR 2.0 first (cheaper)
        let seedOK = await testVolcResource(baseValues: baseValues, resourceId: seedId, options: options)
        guard !Task.isCancelled else { return }

        if seedOK {
            var values = baseValues
            values["resourceId"] = VolcanoASRConfig.resourceIdAuto
            values["resolvedResourceId"] = seedId
            saveASRCredentialsQuietly(values)
            asrTestStatus = .success
            return
        }

        // Seed 2.0 failed, try bigasr
        let bigOK = await testVolcResource(baseValues: baseValues, resourceId: bigId, options: options)
        guard !Task.isCancelled else { return }

        if bigOK {
            var values = baseValues
            values["resourceId"] = VolcanoASRConfig.resourceIdAuto
            values["resolvedResourceId"] = bigId
            saveASRCredentialsQuietly(values)
            asrTestStatus = .success
            volcResourceHint = L(
                "当前使用大模型版本，开通「模型 2.0」可节省约 80% 费用，识别效果相同",
                "Using bigmodel tier. Enable \"Model 2.0\" for ~80% cost savings with identical quality"
            )
            return
        }

        // Both failed
        asrTestStatus = .failed(L("连接失败，请检查 App ID 和 Access Token", "Connection failed, check App ID & Access Token"))
    }

    private func testVolcResource(baseValues: [String: String], resourceId: String, options: ASRRequestOptions) async -> Bool {
        var values = baseValues
        values["resourceId"] = resourceId
        guard let config = VolcanoASRConfig(credentials: values) else { return false }
        let client = VolcASRClient()
        do {
            try await client.connect(config: config, options: options)
            await client.disconnect()
            return true
        } catch {
            return false
        }
    }

    private func saveASRCredentialsQuietly(_ values: [String: String]) {
        do {
            try KeychainService.saveASRCredentials(for: .volcano, values: values)
            KeychainService.selectedASRProvider = .volcano
            asrCredentialValues = values
            savedASRValues = values
            editedFields = []
            hasStoredASR = true
            isEditingASR = false
        } catch {}
    }

    private static func describeConnectionError(_ error: Error) -> String {
        if let volc = error as? VolcASRError, case .serverRejected(_, let message) = volc {
            return message ?? L("服务器拒绝连接", "Server rejected")
        }
        if let volc = error as? VolcProtocolError, case .serverError(let code, let message) = volc {
            let desc = message ?? L("服务器错误", "Server error")
            return code.map { "\(desc) (\($0))" } ?? desc
        }
        if let urlError = error as? URLError {
            switch urlError.code {
            case .notConnectedToInternet: return L("网络未连接", "No internet")
            case .timedOut: return L("连接超时", "Timed out")
            case .cannotFindHost, .cannotConnectToHost: return L("无法连接服务器", "Cannot reach server")
            default: return urlError.localizedDescription
            }
        }
        return L("连接失败", "Connection failed") + ": " + error.localizedDescription
    }

    private func currentASRRequestOptions(enablePunc: Bool) -> ASRRequestOptions {
        let biasSettings = ASRBiasSettingsStorage.load()
        return ASRRequestOptions(
            enablePunc: enablePunc,
            hotwords: HotwordStorage.load(),
            boostingTableID: biasSettings.boostingTableID,
            bypassProxy: ProxyBypassMode.current.bypassASR
        )
    }
}
