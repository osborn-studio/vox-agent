import Foundation

struct CustomASRConfig: ASRProviderConfig, Sendable {

    static let provider = ASRProvider.custom
    static var displayName: String { L("自定义", "Custom") }

    static var credentialFields: [CredentialField] {[
        CredentialField(key: "endpointURL", label: "Endpoint URL", placeholder: L("wss://... 或 https://...", "wss://... or https://..."), isSecure: false, isOptional: false, defaultValue: ""),
        CredentialField(key: "apiKey", label: "API Key / Token", placeholder: L("认证密钥", "Auth key"), isSecure: true, isOptional: true, defaultValue: ""),
        CredentialField(key: "secretKey", label: "Secret Key", placeholder: L("可选", "Optional"), isSecure: true, isOptional: true, defaultValue: ""),
        CredentialField(key: "appId", label: "App ID", placeholder: L("可选", "Optional"), isSecure: false, isOptional: true, defaultValue: ""),
        CredentialField(key: "region", label: "Region", placeholder: L("可选", "Optional"), isSecure: false, isOptional: true, defaultValue: ""),
    ]}

    let endpointURL: String
    let apiKey: String?
    let secretKey: String?
    let appId: String?
    let region: String?

    init?(credentials: [String: String]) {
        guard let url = credentials["endpointURL"], !url.isEmpty else { return nil }
        self.endpointURL = url
        self.apiKey = credentials["apiKey"]
        self.secretKey = credentials["secretKey"]
        self.appId = credentials["appId"]
        self.region = credentials["region"]
    }

    func toCredentials() -> [String: String] {
        var result = ["endpointURL": endpointURL]
        if let v = apiKey, !v.isEmpty { result["apiKey"] = v }
        if let v = secretKey, !v.isEmpty { result["secretKey"] = v }
        if let v = appId, !v.isEmpty { result["appId"] = v }
        if let v = region, !v.isEmpty { result["region"] = v }
        return result
    }

    var isValid: Bool { !endpointURL.isEmpty }
}
