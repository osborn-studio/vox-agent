import Foundation

struct SonioxASRConfig: ASRProviderConfig, Sendable {

    static let provider = ASRProvider.soniox
    static let displayName = "Soniox"
    static let defaultModel = "stt-rt-v4"
    static let supportedModels = [
        "stt-rt-v4",
        "stt-rt-v3",
    ]

    static var credentialFields: [CredentialField] {[
        CredentialField(
            key: "apiKey",
            label: L("API Key (默认 \(defaultModel))", "API Key (uses \(defaultModel))"),
            placeholder: L("粘贴 API Key", "Paste your API Key"),
            isSecure: true,
            isOptional: false,
            defaultValue: ""
        ),
    ]}

    let apiKey: String
    let model: String

    init?(credentials: [String: String]) {
        guard let apiKey = Self.sanitized(credentials["apiKey"]) else {
            return nil
        }

        let rawModel = Self.sanitized(credentials["model"])?.lowercased() ?? ""
        self.apiKey = apiKey
        self.model = Self.supportedModels.contains(rawModel) ? rawModel : Self.defaultModel
    }

    func toCredentials() -> [String: String] {
        [
            "apiKey": apiKey,
            "model": model,
        ]
    }

    var isValid: Bool {
        !apiKey.isEmpty && Self.supportedModels.contains(model)
    }

    private static func sanitized(_ value: String?) -> String? {
        guard let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty
        else {
            return nil
        }
        return trimmed
    }
}
