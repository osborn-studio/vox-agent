import SwiftUI

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// MARK: - LLM Settings Card
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

struct LLMSettingsCard: View, SettingsCardHelpers {

    @State private var selectedLLMProvider: LLMProvider = .doubao
    @State private var llmCredentialValues: [String: String] = [:]
    @State private var savedLLMValues: [String: String] = [:]
    @State private var editedFields: Set<String> = []
    @State private var llmTestStatus: SettingsTestStatus = .idle
    @State private var isEditingLLM = true
    @State private var hasStoredLLM = false
    @State private var testTask: Task<Void, Never>?
    /// Tracks which credential fields are in "custom input" mode (value not in preset options).
    @State private var customModeFields: Set<String> = []
    @State private var isRefreshingModels = false
    @State private var refreshedModels: [String] = []

    private var currentLLMFields: [CredentialField] {
        LLMProviderRegistry.configType(for: selectedLLMProvider)?.credentialFields ?? []
    }

    /// Effective values: saved base + dirty edits overlaid.
    private var effectiveLLMValues: [String: String] {
        var result = savedLLMValues
        for key in editedFields {
            result[key] = llmCredentialValues[key] ?? ""
        }
        return result
    }

    private var hasLLMCredentials: Bool {
        let required = currentLLMFields.filter { !$0.isOptional }
        let effective = effectiveLLMValues
        return required.allSatisfy { field in
            !(effective[field.key] ?? "").isEmpty
        }
    }

    // MARK: Body

    var body: some View {
        settingsGroupCard(L("LLM 文本处理", "LLM Settings"), icon: "gearshape.fill") {
            HStack {
                llmProviderPicker
                Spacer()
                // Refresh models from API
                Button {
                    refreshModels()
                } label: {
                    HStack(spacing: 3) {
                        if isRefreshingModels {
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
                .disabled(isRefreshingModels)
                .help(L("从 API 获取最新可用模型列表", "Fetch latest models from API"))
            }

            // Show fetched models
            if !refreshedModels.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text(L("可用模型 (\(refreshedModels.count))", "Available (\(refreshedModels.count))"))
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(TF.settingsTextTertiary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 4) {
                            ForEach(refreshedModels, id: \.self) { model in
                                Button {
                                    // Click to fill model name
                                    llmCredentialValues["model"] = model
                                    editedFields.insert("model")
                                    customModeFields.remove("model")
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

            SettingsDivider()

            if hasLLMCredentials && !isEditingLLM {
                credentialSummaryCard(rows: llmSummaryRows)
            } else {
                dynamicCredentialFields
            }

            HStack(spacing: 8) {
                Spacer()
                testButton(L("测试连接", "Test"), status: llmTestStatus) { testLLMConnection() }
                    .disabled(!hasLLMCredentials)
                if hasLLMCredentials && !isEditingLLM {
                    secondaryButton(L("修改", "Edit")) {
                        testTask?.cancel()
                        llmTestStatus = .idle
                        llmCredentialValues = [:]
                        editedFields = []
                        isEditingLLM = true
                        syncCustomModeFields()
                    }
                } else {
                    if hasLLMCredentials && hasStoredLLM {
                        secondaryButton(L("取消", "Cancel")) {
                            testTask?.cancel()
                            llmTestStatus = .idle
                            loadLLMCredentials()
                        }
                    }
                    primaryButton(L("保存", "Save")) { saveLLMCredentials() }
                        .disabled(!hasLLMCredentials)
                }
            }
            .padding(.top, 12)
        }
        .task {
            loadLLMCredentials()
            // Load cached models
            let cached = await ModelListFetcher.shared.models(for: selectedLLMProvider)
            if !cached.isEmpty { refreshedModels = cached }
            syncCustomModeFields()
        }
    }

    private func refreshModels() {
        isRefreshingModels = true
        let provider = selectedLLMProvider
        let values = effectiveLLMValues
        Task {
            let models = await ModelListFetcher.shared.refresh(provider: provider, credentials: values)
            await MainActor.run {
                refreshedModels = provider == selectedLLMProvider ? models : []
                syncCustomModeFields()
                isRefreshingModels = false
            }
        }
    }

    // MARK: - Provider Picker

    private var llmProviderPicker: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L("服务商", "Provider").uppercased())
                .font(.system(size: 10, weight: .semibold))
                .tracking(0.8)
                .foregroundStyle(TF.settingsTextTertiary)
            settingsDropdown(
                selection: Binding(
                    get: { selectedLLMProvider.rawValue },
                    set: { if let p = LLMProvider(rawValue: $0) { selectedLLMProvider = p } }
                ),
                options: LLMProvider.allCases.map { ($0.rawValue, $0.displayName) }
            )
        }
        .padding(.vertical, 6)
        .onChange(of: selectedLLMProvider) { _, newProvider in
            testTask?.cancel()
            llmTestStatus = .idle
            isEditingLLM = true
            loadLLMCredentialsForProvider(newProvider)

            // Update model list for new provider
            Task {
                let cached = await ModelListFetcher.shared.models(for: newProvider)
                await MainActor.run {
                    refreshedModels = cached
                    syncCustomModeFields()
                }
            }

            // Auto-save provider switch if target already has credentials
            if hasLLMCredentials {
                KeychainService.selectedLLMProvider = newProvider
            }
        }
    }

    // MARK: - Credential Fields

    private var dynamicCredentialFields: some View {
        let fields = currentLLMFields
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
        let dynamicModelOptions: [FieldOption] = field.key == "model"
            ? refreshedModels.map { FieldOption(value: $0, label: $0) }
            : []
        let fieldOptions = dynamicModelOptions.isEmpty ? field.options : dynamicModelOptions
        let allowsCustomInput = field.allowCustomInput || (!dynamicModelOptions.isEmpty && field.key == "model")

        if !fieldOptions.isEmpty && allowsCustomInput {
            // Combobox: preset dropdown + "Custom" entry that reveals a text field.
            let allOptions = fieldOptions + [FieldOption(value: CredentialField.customValue, label: L("自定义…", "Custom…"))]
            let pickerBinding = Binding<String>(
                get: {
                    if customModeFields.contains(field.key) {
                        return CredentialField.customValue
                    }
                    let val = llmCredentialValues[field.key] ?? ""
                    return val.isEmpty ? (savedLLMValues[field.key] ?? field.defaultValue) : val
                },
                set: { newValue in
                    if newValue == CredentialField.customValue {
                        customModeFields.insert(field.key)
                        llmCredentialValues[field.key] = ""
                        editedFields.insert(field.key)
                    } else {
                        customModeFields.remove(field.key)
                        llmCredentialValues[field.key] = newValue
                        editedFields.insert(field.key)
                    }
                }
            )
            let customBinding = Binding<String>(
                get: { llmCredentialValues[field.key] ?? "" },
                set: {
                    llmCredentialValues[field.key] = $0
                    editedFields.insert(field.key)
                }
            )
            VStack(alignment: .leading, spacing: 4) {
                settingsPickerField(field.label, selection: pickerBinding, options: allOptions)
                if customModeFields.contains(field.key) {
                    settingsField("", text: customBinding, prompt: field.placeholder)
                }
            }
        } else if !fieldOptions.isEmpty {
            let pickerBinding = Binding<String>(
                get: {
                    let val = llmCredentialValues[field.key] ?? ""
                    return val.isEmpty ? (savedLLMValues[field.key] ?? field.defaultValue) : val
                },
                set: {
                    llmCredentialValues[field.key] = $0
                    editedFields.insert(field.key)
                }
            )
            settingsPickerField(field.label, selection: pickerBinding, options: fieldOptions)
        } else if field.isSecure {
            let binding = Binding<String>(
                get: { llmCredentialValues[field.key] ?? "" },
                set: {
                    llmCredentialValues[field.key] = $0
                    editedFields.insert(field.key)
                }
            )
            let savedVal = savedLLMValues[field.key] ?? ""
            let placeholder = savedVal.isEmpty ? field.placeholder : maskedSecret(savedVal)
            settingsSecureField(field.label, text: binding, prompt: placeholder)
        } else {
            // Non-secure text field: show saved/default value as actual text, not placeholder.
            let binding = Binding<String>(
                get: {
                    let val = llmCredentialValues[field.key] ?? ""
                    if val.isEmpty {
                        return savedLLMValues[field.key] ?? field.defaultValue
                    }
                    return val
                },
                set: {
                    llmCredentialValues[field.key] = $0
                    editedFields.insert(field.key)
                }
            )
            settingsField(field.label, text: binding, prompt: field.placeholder)
        }
    }

    private var llmSummaryRows: [(String, String)] {
        var rows: [(String, String)] = []
        for field in currentLLMFields {
            let val = llmCredentialValues[field.key] ?? ""
            guard !val.isEmpty else { continue }
            let display = field.isSecure ? maskedSecret(val) : val
            rows.append((field.label, display))
        }
        return rows
    }

    // MARK: - Data

    /// Detects which combobox fields hold values not matching any preset option,
    /// and puts them into custom input mode so the UI shows the text field.
    private func syncCustomModeFields() {
        var custom: Set<String> = []
        for field in currentLLMFields {
            let dynamicModelOptions: [FieldOption] = field.key == "model"
                ? refreshedModels.map { FieldOption(value: $0, label: $0) }
                : []
            let options = dynamicModelOptions.isEmpty ? field.options : dynamicModelOptions
            let allowsCustomInput = field.allowCustomInput || (!dynamicModelOptions.isEmpty && field.key == "model")
            guard allowsCustomInput && !options.isEmpty else { continue }

            let val = llmCredentialValues[field.key]
                ?? savedLLMValues[field.key]
                ?? field.defaultValue
            if !val.isEmpty && !options.contains(where: { $0.value == val }) {
                custom.insert(field.key)
            }
        }
        customModeFields = custom
    }

    private func loadLLMCredentials() {
        selectedLLMProvider = KeychainService.selectedLLMProvider
        loadLLMCredentialsForProvider(selectedLLMProvider)
    }

    private func loadLLMCredentialsForProvider(_ provider: LLMProvider) {
        testTask?.cancel()
        editedFields = []
        if let values = KeychainService.loadLLMCredentials(for: provider) {
            llmCredentialValues = values
            savedLLMValues = values
            hasStoredLLM = true
            isEditingLLM = !hasLLMCredentials
        } else {
            var defaults: [String: String] = [:]
            let fields = LLMProviderRegistry.configType(for: provider)?.credentialFields ?? []
            for field in fields where !field.defaultValue.isEmpty {
                defaults[field.key] = field.defaultValue
            }
            llmCredentialValues = defaults
            savedLLMValues = [:]
            hasStoredLLM = false
            isEditingLLM = true
        }
        syncCustomModeFields()
    }

    private func saveLLMCredentials() {
        let values = effectiveLLMValues
        do {
            try KeychainService.saveLLMCredentials(for: selectedLLMProvider, values: values)
            KeychainService.selectedLLMProvider = selectedLLMProvider
            llmCredentialValues = values
            savedLLMValues = values
            editedFields = []
            hasStoredLLM = true
            isEditingLLM = false
            syncCustomModeFields()
            llmTestStatus = .saved
        } catch {
            llmTestStatus = .failed(L("保存失败", "Save failed"))
        }
    }

    private func testLLMConnection() {
        testTask?.cancel()
        llmTestStatus = .testing
        let testValues = effectiveLLMValues
        let provider = selectedLLMProvider
        testTask = Task {
            do {
                guard let configType = LLMProviderRegistry.configType(for: provider),
                      let config = configType.init(credentials: testValues)
                else {
                    guard !Task.isCancelled else { return }
                    llmTestStatus = .failed(L("配置无效", "Invalid config"))
                    return
                }
                let llmConfig = config.toLLMConfig()
                let client: any LLMClient = provider == .claude
                    ? ClaudeChatClient()
                    : DoubaoChatClient(provider: provider)
                let reply = try await client.process(text: "hi", prompt: "{text}", config: llmConfig)
                guard !Task.isCancelled else { return }
                llmTestStatus = .success
                NSLog("[Settings] LLM test OK (%@): %d chars", provider.rawValue, reply.count)
            } catch {
                guard !Task.isCancelled else { return }
                NSLog("[Settings] LLM test failed (%@): %@", provider.rawValue, String(describing: error))
                llmTestStatus = .failed(error.localizedDescription)
            }
        }
    }
}
