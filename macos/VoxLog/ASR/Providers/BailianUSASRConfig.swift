import Foundation

/// Bailian US region — uses OpenAI-compatible HTTP endpoint (not WebSocket).
/// dashscope-us.aliyuncs.com/compatible-mode/v1/audio/transcriptions
struct BailianUSASRConfig: ASRProviderConfig, Sendable {

    static let provider = ASRProvider.bailianUS
    static let displayName = L("百炼 US", "Bailian US")
    static let defaultModel = "qwen3-asr-flash-us"
    static let defaultBaseURL = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"

    static var credentialFields: [CredentialField] {[
        CredentialField(
            key: "apiKey",
            label: "API Key",
            placeholder: "sk-...",
            isSecure: true,
            isOptional: false,
            defaultValue: ""
        ),
        CredentialField(
            key: "model",
            label: "Model",
            placeholder: defaultModel,
            isSecure: false,
            isOptional: false,
            defaultValue: defaultModel
        ),
        CredentialField(
            key: "baseURL",
            label: "Base URL",
            placeholder: defaultBaseURL,
            isSecure: false,
            isOptional: true,
            defaultValue: defaultBaseURL
        ),
    ]}

    let apiKey: String
    let model: String
    let baseURL: String

    init?(credentials: [String: String]) {
        guard let apiKey = credentials["apiKey"]?.trimmingCharacters(in: .whitespacesAndNewlines),
              !apiKey.isEmpty
        else { return nil }
        self.apiKey = apiKey
        self.model = credentials["model"]?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            ? credentials["model"]! : Self.defaultModel
        self.baseURL = credentials["baseURL"]?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            ? credentials["baseURL"]! : Self.defaultBaseURL
    }

    func toCredentials() -> [String: String] {
        ["apiKey": apiKey, "model": model, "baseURL": baseURL]
    }

    var isValid: Bool { !apiKey.isEmpty }
}
