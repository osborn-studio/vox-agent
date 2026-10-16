import Foundation

struct TencentASRConfig: ASRProviderConfig, Sendable {

    static let provider = ASRProvider.tencent
    static var displayName: String { L("腾讯云", "Tencent Cloud") }

    static var credentialFields: [CredentialField] {[
        CredentialField(key: "secretId", label: "Secret ID", placeholder: L("密钥 ID", "Key ID"), isSecure: false, isOptional: false, defaultValue: ""),
        CredentialField(key: "secretKey", label: "Secret Key", placeholder: L("密钥", "Secret"), isSecure: true, isOptional: false, defaultValue: ""),
        CredentialField(key: "appId", label: "App ID", placeholder: L("应用 ID", "App ID"), isSecure: false, isOptional: false, defaultValue: ""),
    ]}

    let secretId: String
    let secretKey: String
    let appId: String

    init?(credentials: [String: String]) {
        guard let sid = credentials["secretId"], !sid.isEmpty,
              let key = credentials["secretKey"], !key.isEmpty,
              let appId = credentials["appId"], !appId.isEmpty
        else { return nil }
        self.secretId = sid
        self.secretKey = key
        self.appId = appId
    }

    func toCredentials() -> [String: String] {
        ["secretId": secretId, "secretKey": secretKey, "appId": appId]
    }

    var isValid: Bool { !secretId.isEmpty && !secretKey.isEmpty && !appId.isEmpty }
}
