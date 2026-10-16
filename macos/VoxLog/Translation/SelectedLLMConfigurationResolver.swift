import Foundation

struct TranslationConfiguration: Sendable {
    let provider: LLMProvider
    let endpoint: URL
    let model: String
    let credential: String
    let generation: UInt64
    let bypassProxy: Bool

    init(provider: LLMProvider, baseURL: String, model: String, credential: String,
         generation: UInt64, bypassProxy: Bool) throws {
        guard baseURL.utf8.count <= 4096, credential.utf8.count <= 8192,
              var parts = URLComponents(string: baseURL),
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.scheme == "https" || (parts.scheme == "http" && ["localhost", "127.0.0.1", "::1", "[::1]"].contains(host)),
              !model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              model.utf8.count <= 256, !credential.contains("\r"), !credential.contains("\n"),
              !provider.requiresAPIKey || !credential.isEmpty else { throw TranslationFailure.configuration }
        parts.host = host.lowercased()
        while parts.path.hasSuffix("/") { parts.path.removeLast() }
        parts.path += provider == .claude ? "/messages" : "/chat/completions"
        guard let url = parts.url else { throw TranslationFailure.configuration }
        self.provider = provider; endpoint = url; self.model = model; self.credential = credential
        self.generation = generation; self.bypassProxy = bypassProxy
    }
    var fingerprint: String { "p\(TranslationProtocol.promptVersion)|\(provider.rawValue)|\(endpoint.absoluteString)|\(model)|\(generation)|\(bypassProxy)" }
}

/// Synchronous on the UI/settings owner. Snapshot reads selection exactly once and
/// only that provider's credentials; injectable reads make this contract testable.
@MainActor enum SelectedLLMConfigurationResolver {
    static func snapshot() throws -> TranslationConfiguration {
        try KeychainService.withLLMConfigurationLock {
            try snapshot(selected: { KeychainService.selectedLLMProvider }, load: { KeychainService.loadLLMCredentials(for: $0) })
        }
    }
    static func snapshot(
        selected: () -> LLMProvider,
        load: (LLMProvider) -> [String: String]?,
        generation: @MainActor () -> UInt64 = { TranslationSettings.shared.generation },
        bypass: () -> Bool = { ProxyBypassMode.current.bypassLLM }
    ) throws -> TranslationConfiguration {
        let epoch = generation(), provider = selected()
        guard let values = load(provider) else { throw TranslationFailure.configuration }
        guard epoch == generation() else { throw TranslationFailure.configuration }
        return try TranslationConfiguration(provider: provider,
            baseURL: values["baseURL"].flatMap { $0.isEmpty ? nil : $0 } ?? provider.defaultBaseURL,
            model: values["model"] ?? "", credential: values["apiKey"] ?? "",
            generation: epoch, bypassProxy: bypass())
    }
}

@MainActor @Observable final class TranslationSettings {
    static let shared = TranslationSettings()
    static let preferenceKey = "voxagent_message_translation_development"
    private(set) var generation: UInt64 = 0
    private(set) var enabled: Bool
    private var observers: [UUID: () -> Void] = [:]
    private let defaults: UserDefaults
    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults; enabled = defaults.bool(forKey: Self.preferenceKey)
    }
    func setEnabled(_ value: Bool) {
        guard enabled != value else { return }
        enabled = value; defaults.set(value, forKey: Self.preferenceKey); configurationChanged()
    }
    func configurationChanged() {
        generation &+= 1
        for action in observers.values { action() }
    }
    func observe(_ action: @escaping () -> Void) -> UUID {
        let id = UUID(); observers[id] = action; return id
    }
    func removeObserver(_ id: UUID) { observers.removeValue(forKey: id) }
}
