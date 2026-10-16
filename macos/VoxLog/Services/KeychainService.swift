import Foundation
import Security
import LocalAuthentication

enum KeychainService {

    /// UI identity checks must never prompt or fall back to legacy file storage.
    static func loadScalarWithoutInteraction(key: String) -> String? {
        var query = keychainQuery(service: keychainScalarService, account: key)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        let context = LAContext()
        context.interactionNotAllowed = true
        query[kSecUseAuthenticationContext as String] = context
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static let lock = NSLock()
    private static let llmConfigurationLock = NSRecursiveLock()
    static func withLLMConfigurationLock<T>(_ action: () throws -> T) rethrows -> T {
        llmConfigurationLock.lock(); defer { llmConfigurationLock.unlock() }
        return try action()
    }
    private static func translationConfigurationChanged() {
        if Thread.isMainThread { MainActor.assumeIsolated { TranslationSettings.shared.configurationChanged() } }
        else { DispatchQueue.main.sync { MainActor.assumeIsolated { TranslationSettings.shared.configurationChanged() } } }
    }
    /// Display metadata only: this method never consults Keychain.
    static func llmDisplayConfiguration(for provider: LLMProvider) -> (model: String, endpoint: String) {
        let values = loadAll()[llmStorageKey(for: provider)] as? [String: String] ?? [:]
        let raw = values["baseURL"].flatMap { $0.isEmpty ? nil : $0 } ?? provider.defaultBaseURL
        // Credentials/query/userinfo are never rendered even in an invalid saved URL.
        var parts = URLComponents(string: raw)
        parts?.user = nil; parts?.password = nil; parts?.query = nil; parts?.fragment = nil
        if var endpoint = parts {
            while endpoint.path.hasSuffix("/") { endpoint.path.removeLast() }
            endpoint.path += provider == .claude ? "/messages" : "/chat/completions"
            parts = endpoint
        }
        return (values["model"] ?? L("尚未配置", "Not configured"), parts?.string ?? L("无效地址", "Invalid endpoint"))
    }
    private static var cachedCredentials: [String: Any]?
    private static var keychainServicePrefix = "com.voxlog"
    private static var appSupportDirectoryOverride: URL?
    private static var keychainScalarService: String { "\(keychainServicePrefix).scalar" }
    private static var keychainGroupedService: String { "\(keychainServicePrefix).grouped" }

    private static var credentialsURL: URL {
        let dir = appSupportDirectoryOverride ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("VoxLog", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("credentials.json")
    }

    static var credentialsURLForTests: URL { credentialsURL }

    static func configureForTests(keychainServicePrefix: String, appSupportDirectory: URL) {
        lock.lock()
        defer { lock.unlock() }
        self.keychainServicePrefix = keychainServicePrefix
        self.appSupportDirectoryOverride = appSupportDirectory
        cachedCredentials = nil
    }

    static func resetTestConfiguration() {
        lock.lock()
        defer { lock.unlock() }
        keychainServicePrefix = "com.voxlog"
        appSupportDirectoryOverride = nil
        cachedCredentials = nil
    }

    // MARK: - Core read/write (now supports nested objects)

    /// Load without acquiring lock — caller must hold `lock`.
    private static func _loadAllUnlocked() -> [String: Any] {
        if let cached = cachedCredentials { return cached }
        guard let data = try? Data(contentsOf: credentialsURL),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [:] }
        cachedCredentials = dict
        return dict
    }

    /// Public read access for VoxLog custom keys (vl_*)
    static func loadAll_voxlog() -> [String: Any] {
        loadAll()
    }

    /// Thread-safe load.
    private static func loadAll() -> [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        return _loadAllUnlocked()
    }

    private static func saveAll(_ dict: [String: Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: dict, options: [.sortedKeys])
        try data.write(to: credentialsURL, options: .atomic)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: credentialsURL.path
        )
    }

    // MARK: - Scalar key-value (for LLM keys and misc)

    static func save(key: String, value: String) throws {
        try saveSecureString(value, service: keychainScalarService, account: key)
    }

    static func load(key: String) -> String? {
        loadSecureString(service: keychainScalarService, account: key)
    }

    /// New encrypted draft keys must distinguish missing from denied/locked/error.
    /// No legacy credentials.json fallback and no overwrite when creating a key.
    static func loadSecureScalarStrict(key: String) throws -> String? {
        var query = keychainQuery(service: keychainScalarService, account: key)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw KeychainError.readFailed(status) }
        guard let bytes = item as? Data, let value = String(data: bytes, encoding: .utf8) else { throw KeychainError.invalidEncoding }
        return value
    }
    static func addSecureScalarIfAbsent(key: String, value: String) throws {
        guard let bytes = value.data(using: .utf8) else { throw KeychainError.invalidEncoding }
        var query = keychainQuery(service: keychainScalarService, account: key)
        query[kSecValueData as String] = bytes
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError.saveFailed(status) }
    }

    @discardableResult
    static func delete(key: String) -> Bool {
        deleteSecureValue(service: keychainScalarService, account: key)
    }

    // MARK: - Selected ASR Provider (UserDefaults)

    private static let selectedProviderKey = "tf_selectedASRProvider"

    static var selectedASRProvider: ASRProvider {
        get {
            guard let raw = UserDefaults.standard.string(forKey: selectedProviderKey),
                  let provider = ASRProvider(rawValue: raw)
            else { return .volcano }
            return provider
        }
        set {
            let previous = selectedASRProvider
            UserDefaults.standard.set(newValue.rawValue, forKey: selectedProviderKey)
            guard previous != newValue else { return }
            NotificationCenter.default.post(name: .asrProviderDidChange, object: newValue)
        }
    }

    // MARK: - Last BYOK Provider (for edition switching)

    private static let lastBYOKProviderKey = "tf_lastBYOKProvider"

    static var lastBYOKProvider: ASRProvider {
        get {
            guard let raw = UserDefaults.standard.string(forKey: lastBYOKProviderKey),
                  let provider = ASRProvider(rawValue: raw)
            else { return .volcano }
            return provider
        }
        set {
            UserDefaults.standard.set(newValue.rawValue, forKey: lastBYOKProviderKey)
        }
    }

    // MARK: - ASR Credentials (provider-aware)

    private static func asrStorageKey(for provider: ASRProvider) -> String {
        "tf_asr_\(provider.rawValue)"
    }

    static func saveASRCredentials(for provider: ASRProvider, values: [String: String]) throws {
        lock.lock()
        defer { lock.unlock() }
        var dict = _loadAllUnlocked()
        let storageKey = asrStorageKey(for: provider)
        let split = splitCredentials(values, using: ASRProviderRegistry.configType(for: provider)?.credentialFields ?? [])
        if split.secure.isEmpty {
            _ = deleteSecureValue(service: keychainGroupedService, account: storageKey)
        } else {
            try saveSecureValues(split.secure, account: storageKey)
        }
        if split.plaintext.isEmpty {
            dict.removeValue(forKey: storageKey)
        } else {
            dict[storageKey] = split.plaintext
        }
        try saveAll(dict)
        cachedCredentials = dict
    }

    static func loadASRCredentials(for provider: ASRProvider) -> [String: String]? {
        let dict = loadAll()
        let storageKey = asrStorageKey(for: provider)
        let plaintext = dict[storageKey] as? [String: String] ?? [:]
        let secure = loadSecureValues(account: storageKey)
        let merged = plaintext.merging(secure) { _, secure in secure }
        return merged.isEmpty ? nil : merged
    }

    static func loadASRConfig(for provider: ASRProvider) -> (any ASRProviderConfig)? {
        guard let configType = ASRProviderRegistry.configType(for: provider) else {
            return nil
        }

        if let values = loadASRCredentials(for: provider) {
            return configType.init(credentials: values)
        }

        // Fallback: build config from default field values (e.g. Apple ASR needs no API key)
        let defaultValues: [String: String] = Dictionary(
            uniqueKeysWithValues: configType.credentialFields.compactMap { field in
                guard !field.defaultValue.isEmpty else { return nil }
                return (field.key, field.defaultValue)
            }
        )

        if defaultValues.isEmpty && configType.credentialFields.isEmpty {
            return configType.init(credentials: [:])
        }

        return configType.init(credentials: defaultValues)
    }

    /// Load config for the currently selected provider.
    static func loadSelectedASRConfig() -> (any ASRProviderConfig)? {
        loadASRConfig(for: selectedASRProvider)
    }

    // MARK: - Legacy ASR convenience (volcano-specific, kept for migration)

    static func saveASRCredentials(appKey: String, accessKey: String, resourceId: String) throws {
        try saveASRCredentials(for: .volcano, values: [
            "appKey": appKey,
            "accessKey": accessKey,
            "resourceId": resourceId,
        ])
    }

    static func loadASRConfig() -> VolcanoASRConfig? {
        loadASRConfig(for: .volcano) as? VolcanoASRConfig
    }

    // MARK: - Selected LLM Provider (UserDefaults)

    private static let selectedLLMProviderKey = "tf_selectedLLMProvider"

    static var selectedLLMProvider: LLMProvider {
        get {
            guard let raw = UserDefaults.standard.string(forKey: selectedLLMProviderKey),
                  let provider = LLMProvider(rawValue: raw)
            else { return .doubao }
            return provider
        }
        set {
            withLLMConfigurationLock { UserDefaults.standard.set(newValue.rawValue, forKey: selectedLLMProviderKey) }
            translationConfigurationChanged()
        }
    }

    // MARK: - LLM Credentials (provider-aware)

    private static func llmStorageKey(for provider: LLMProvider) -> String {
        "tf_llm_\(provider.rawValue)"
    }

    static func saveLLMCredentials(for provider: LLMProvider, values: [String: String]) throws {
        llmConfigurationLock.lock()
        defer { llmConfigurationLock.unlock(); translationConfigurationChanged() }
        lock.lock()
        defer { lock.unlock() }
        var dict = _loadAllUnlocked()
        let storageKey = llmStorageKey(for: provider)
        let split = splitCredentials(values, using: LLMProviderRegistry.configType(for: provider)?.credentialFields ?? [])
        if split.secure.isEmpty {
            _ = deleteSecureValue(service: keychainGroupedService, account: storageKey)
        } else {
            try saveSecureValues(split.secure, account: storageKey)
        }
        if split.plaintext.isEmpty {
            dict.removeValue(forKey: storageKey)
        } else {
            dict[storageKey] = split.plaintext
        }
        try saveAll(dict)
        cachedCredentials = dict
    }

    static func loadLLMCredentials(for provider: LLMProvider) -> [String: String]? {
        let dict = loadAll()
        let storageKey = llmStorageKey(for: provider)
        let plaintext = dict[storageKey] as? [String: String] ?? [:]
        let secure = loadSecureValues(account: storageKey)
        let merged = plaintext.merging(secure) { _, secure in secure }
        return merged.isEmpty ? nil : merged
    }

    static func loadLLMProviderConfig(for provider: LLMProvider) -> (any LLMProviderConfig)? {
        guard let values = loadLLMCredentials(for: provider),
              let configType = LLMProviderRegistry.configType(for: provider)
        else { return nil }
        return configType.init(credentials: values)
    }

    /// Load config for the currently selected LLM provider.
    static func loadSelectedLLMConfig() -> (any LLMProviderConfig)? {
        loadLLMProviderConfig(for: selectedLLMProvider)
    }

    // MARK: - Notion Sync

    private static let notionTokenKey = "voxlog_notion_api_token"

    static func saveNotionToken(_ token: String) throws {
        try save(key: notionTokenKey, value: token.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    static func loadNotionToken() -> String? {
        guard let token = load(key: notionTokenKey),
              !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return nil }
        return token
    }

    @discardableResult
    static func deleteNotionToken() -> Bool {
        delete(key: notionTokenKey)
    }

    // MARK: - LLM Config convenience (backward compat)

    static func saveLLMCredentials(apiKey: String, model: String, baseURL: String = "") throws {
        try saveLLMCredentials(for: .doubao, values: [
            "apiKey": apiKey, "model": model, "baseURL": baseURL,
        ])
    }

    /// Load LLMConfig for the currently selected provider.
    static func loadLLMConfig() -> LLMConfig? {
        guard let config = loadSelectedLLMConfig() else { return nil }
        return config.toLLMConfig()
    }

    // MARK: - Migration (call once at app launch)

    /// Migrate legacy flat keys to provider-grouped format,
    /// move Application Support directory, and migrate UserDefaults from old bundle ID.
    static func migrateIfNeeded() {
        migrateAppSupportDirectory()
        migrateUserDefaults()
        lock.lock()
        defer { lock.unlock() }
        let dict = _loadAllUnlocked()

        var migrated = false
        var mutableDict = dict

        // Migrate ASR: tf_appKey/tf_accessKey/tf_resourceId → tf_asr_volcano
        if let appKey = dict["tf_appKey"] as? String, !appKey.isEmpty,
           dict[asrStorageKey(for: .volcano)] == nil {
            let accessKey = dict["tf_accessKey"] as? String ?? ""
            let resourceId = dict["tf_resourceId"] as? String ?? "volc.bigasr.sauc.duration"
            mutableDict[asrStorageKey(for: .volcano)] = [
                "appKey": appKey,
                "accessKey": accessKey,
                "resourceId": resourceId,
            ]
            mutableDict.removeValue(forKey: "tf_appKey")
            mutableDict.removeValue(forKey: "tf_accessKey")
            mutableDict.removeValue(forKey: "tf_resourceId")
            migrated = true
            NSLog("[KeychainService] Migrated legacy ASR credentials to tf_asr_volcano")
        }

        // Migrate mistakenly stored Bailian ASR credentials from tf_asr_aliyun → tf_asr_bailian
        if let aliyunValues = dict[asrStorageKey(for: .aliyun)] as? [String: String],
           let apiKey = aliyunValues["apiKey"], !apiKey.isEmpty,
           dict[asrStorageKey(for: .bailian)] == nil {
            mutableDict[asrStorageKey(for: .bailian)] = aliyunValues
            mutableDict.removeValue(forKey: asrStorageKey(for: .aliyun))
            if selectedASRProvider == .aliyun {
                selectedASRProvider = .bailian
            }
            migrated = true
            NSLog("[KeychainService] Migrated Bailian ASR credentials from tf_asr_aliyun → tf_asr_bailian")
        }

        // Migrate LLM: tf_llmEndpointId → tf_llmModel
        if let endpointId = dict["tf_llmEndpointId"] as? String, !endpointId.isEmpty,
           dict["tf_llmModel"] == nil {
            mutableDict["tf_llmModel"] = endpointId
            mutableDict.removeValue(forKey: "tf_llmEndpointId")
            migrated = true
            NSLog("[KeychainService] Migrated tf_llmEndpointId → tf_llmModel")
        }

        // Migrate LLM: flat keys → tf_llm_doubao (provider-grouped)
        if let apiKey = dict["tf_llmApiKey"] as? String, !apiKey.isEmpty,
           dict[llmStorageKey(for: .doubao)] == nil {
            let model = (dict["tf_llmModel"] as? String) ?? ""
            let baseURL = (dict["tf_llmBaseURL"] as? String) ?? ""
            mutableDict[llmStorageKey(for: .doubao)] = [
                "apiKey": apiKey,
                "model": model,
                "baseURL": baseURL.isEmpty ? LLMProvider.doubao.defaultBaseURL : baseURL,
            ]
            mutableDict.removeValue(forKey: "tf_llmApiKey")
            mutableDict.removeValue(forKey: "tf_llmModel")
            mutableDict.removeValue(forKey: "tf_llmBaseURL")
            migrated = true
            NSLog("[KeychainService] Migrated flat LLM keys to tf_llm_doubao")
        }

        // Migrate MiniMax CN: api.minimax.chat → api.minimaxi.com (old domain was incorrect)
        let minimaxCNKey = llmStorageKey(for: .minimaxCN)
        if var minimaxCreds = mutableDict[minimaxCNKey] as? [String: String],
           let baseURL = minimaxCreds["baseURL"],
           baseURL.contains("api.minimax.chat") {
            minimaxCreds["baseURL"] = baseURL.replacingOccurrences(
                of: "api.minimax.chat", with: "api.minimaxi.com"
            )
            mutableDict[minimaxCNKey] = minimaxCreds
            migrated = true
            NSLog("[KeychainService] Migrated MiniMax CN base URL: api.minimax.chat → api.minimaxi.com")
        }

        let secureFieldsMigrated = migrateSecureCredentialGroups(in: &mutableDict)

        if migrated || secureFieldsMigrated {
            try? saveAll(mutableDict)
            cachedCredentials = mutableDict
        }
    }

    // MARK: - Keychain helpers

    private static func keychainQuery(service: String, account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    private static func saveSecureString(_ value: String, service: String, account: String) throws {
        guard let data = value.data(using: .utf8) else {
            throw KeychainError.invalidEncoding
        }
        try saveSecureData(data, service: service, account: account)
    }

    private static func loadSecureString(service: String, account: String) -> String? {
        guard let data = loadSecureData(service: service, account: account) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func saveSecureValues(_ values: [String: String], account: String) throws {
        let data = try JSONSerialization.data(withJSONObject: values, options: [.sortedKeys])
        try saveSecureData(data, service: keychainGroupedService, account: account)
    }

    private static func loadSecureValues(account: String) -> [String: String] {
        guard let data = loadSecureData(service: keychainGroupedService, account: account),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: String]
        else {
            return [:]
        }
        return object
    }

    private static func saveSecureData(_ data: Data, service: String, account: String) throws {
        var query = keychainQuery(service: service, account: account)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]

        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess { return }
        guard updateStatus == errSecItemNotFound else {
            throw KeychainError.saveFailed(updateStatus)
        }

        query.merge(attributes) { current, _ in current }
        let addStatus = SecItemAdd(query as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw KeychainError.saveFailed(addStatus)
        }
    }

    private static func loadSecureData(service: String, account: String) -> Data? {
        var query = keychainQuery(service: service, account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecSuccess {
            return item as? Data
        }

        // Backward compatibility for short-lived builds that wrote secure fields into credentials.json.
        let fileKey = "\(service).\(account)"
        let all = _loadAllUnlocked()
        if let base64 = all[fileKey] as? String, let data = Data(base64Encoded: base64) {
            return data
        }
        return nil
    }

    @discardableResult
    private static func deleteSecureValue(service: String, account: String) -> Bool {
        let keychainStatus = SecItemDelete(keychainQuery(service: service, account: account) as CFDictionary)

        // Also remove legacy file-backed secure blobs if present.
        let fileKey = "\(service).\(account)"
        var all = _loadAllUnlocked()
        all.removeValue(forKey: fileKey)
        cachedCredentials = all
        try? saveAll(all)
        return keychainStatus == errSecSuccess || keychainStatus == errSecItemNotFound
    }

    // MARK: - Secure field splitting

    private static func splitCredentials(
        _ values: [String: String],
        using fields: [CredentialField]
    ) -> (plaintext: [String: String], secure: [String: String]) {
        let secureKeys = Set(fields.filter(\.isSecure).map(\.key))
        guard !secureKeys.isEmpty else {
            return (values, [:])
        }

        var plaintext: [String: String] = [:]
        var secure: [String: String] = [:]

        for (key, value) in values {
            if secureKeys.contains(key) {
                if !value.isEmpty {
                    secure[key] = value
                }
            } else if !value.isEmpty {
                plaintext[key] = value
            }
        }
        return (plaintext, secure)
    }

    @discardableResult
    private static func migrateSecureCredentialGroups(in dict: inout [String: Any]) -> Bool {
        var migrated = false
        for provider in ASRProvider.allCases {
            migrated = migrateSecureFields(
                in: &dict,
                storageKey: asrStorageKey(for: provider),
                fields: ASRProviderRegistry.configType(for: provider)?.credentialFields ?? []
            ) || migrated
        }
        for provider in LLMProvider.allCases {
            migrated = migrateSecureFields(
                in: &dict,
                storageKey: llmStorageKey(for: provider),
                fields: LLMProviderRegistry.configType(for: provider)?.credentialFields ?? []
            ) || migrated
        }
        return migrated
    }

    @discardableResult
    private static func migrateSecureFields(
        in dict: inout [String: Any],
        storageKey: String,
        fields: [CredentialField]
    ) -> Bool {
        guard let values = dict[storageKey] as? [String: String] else { return false }
        let split = splitCredentials(values, using: fields)
        guard split.plaintext.count != values.count || !split.secure.isEmpty else { return false }
        if !split.secure.isEmpty {
            do {
                try saveSecureValues(split.secure, account: storageKey)
            } catch {
                NSLog("[KeychainService] Failed to migrate secure fields for %@: %@", storageKey, error.localizedDescription)
                return false
            }
        }
        if split.plaintext.isEmpty {
            dict.removeValue(forKey: storageKey)
        } else {
            dict[storageKey] = split.plaintext
        }
        return true
    }

    // MARK: - Application Support Directory Migration

    /// Merge ~/Library/Application Support/TypeFlow/ files into VoxLog/ (one-time, from old project name).
    /// Uses file-level merge instead of directory rename, because other init code may create
    /// the new directory before this migration runs.
    private static func migrateAppSupportDirectory() {
        let fm = FileManager.default
        let appSupport = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let oldDir = appSupport.appendingPathComponent("TypeFlow", isDirectory: true)
        let newDir = appSupport.appendingPathComponent("VoxLog", isDirectory: true)

        // Old directory must exist and contain real data (credentials.json is the marker)
        guard fm.fileExists(atPath: oldDir.appendingPathComponent("credentials.json").path) else { return }

        try? fm.createDirectory(at: newDir, withIntermediateDirectories: true)

        // Move each file from old → new, skipping files that already exist in new
        guard let contents = try? fm.contentsOfDirectory(atPath: oldDir.path) else { return }
        var movedCount = 0
        for item in contents {
            let src = oldDir.appendingPathComponent(item)
            let dst = newDir.appendingPathComponent(item)
            if !fm.fileExists(atPath: dst.path) {
                do {
                    try fm.moveItem(at: src, to: dst)
                    movedCount += 1
                } catch {
                    NSLog("[KeychainService] Failed to migrate %@: %@", item, error.localizedDescription)
                }
            }
        }

        if movedCount > 0 {
            NSLog("[KeychainService] Migrated %d files from TypeFlow → VoxLog", movedCount)
        }

        // Clean up old directory if empty
        if let remaining = try? fm.contentsOfDirectory(atPath: oldDir.path), remaining.isEmpty {
            try? fm.removeItem(at: oldDir)
        }
    }

    // MARK: - UserDefaults Migration (old bundle ID)

    /// Copy tf_ keys from old com.typeflow.app UserDefaults to current domain.
    /// One-time: skips if already migrated (marker key present).
    private static func migrateUserDefaults() {
        let marker = "tf_migratedFromTypeFlow"
        guard !UserDefaults.standard.bool(forKey: marker) else { return }

        guard let oldDefaults = UserDefaults(suiteName: "com.typeflow.app") else { return }
        let oldDict = oldDefaults.dictionaryRepresentation()
        let tfKeys = oldDict.keys.filter { $0.hasPrefix("tf_") }

        guard !tfKeys.isEmpty else {
            UserDefaults.standard.set(true, forKey: marker)
            return
        }

        var count = 0
        for key in tfKeys {
            // Don't overwrite if the new domain already has a value
            if UserDefaults.standard.object(forKey: key) == nil {
                UserDefaults.standard.set(oldDict[key], forKey: key)
                count += 1
            }
        }

        UserDefaults.standard.set(true, forKey: marker)
        if count > 0 {
            NSLog("[KeychainService] Migrated %d UserDefaults keys from com.typeflow.app", count)
        }
    }
}

enum KeychainError: Error {
    case invalidEncoding
    case saveFailed(OSStatus)
    case readFailed(OSStatus)
}
