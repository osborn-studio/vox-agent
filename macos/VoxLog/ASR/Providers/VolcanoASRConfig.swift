import Foundation

struct VolcanoASRConfig: ASRProviderConfig, Sendable {

    static let provider = ASRProvider.volcano
    static var displayName: String { L("火山引擎 (Doubao)", "Volcano (Doubao)") }

    /// 豆包流式语音识别模型 2.0
    static let resourceIdSeedASR = "volc.seedasr.sauc.duration"
    /// 豆包流式语音识别模型 1.0
    static let resourceIdBigASR = "volc.bigasr.sauc.duration"
    /// Auto: prefer 2.0, fall back to 1.0
    static let resourceIdAuto = "auto"

    static var credentialFields: [CredentialField] {[
        CredentialField(key: "appKey", label: "App ID", placeholder: "APPID", isSecure: false, isOptional: false, defaultValue: ""),
        CredentialField(key: "accessKey", label: "Access Token", placeholder: L("访问令牌", "Access token"), isSecure: true, isOptional: false, defaultValue: ""),
        CredentialField(
            key: "resourceId",
            label: L("识别模型", "Model"),
            placeholder: "",
            isSecure: false,
            isOptional: false,
            defaultValue: resourceIdAuto,
            options: [
                FieldOption(value: resourceIdAuto, label: L("自动（优先 2.0，额度用完切 1.0）", "Auto (prefer 2.0, fallback to 1.0)")),
                FieldOption(value: resourceIdSeedASR, label: L("流式语音识别模型 2.0", "Streaming ASR Model 2.0")),
                FieldOption(value: resourceIdBigASR, label: L("流式语音识别大模型", "Streaming ASR Large Model")),
            ]
        ),
    ]}

    let appKey: String
    let accessKey: String
    let resourceId: String
    let uid: String

    init?(credentials: [String: String]) {
        guard let appKey = credentials["appKey"], !appKey.isEmpty,
              let accessKey = credentials["accessKey"], !accessKey.isEmpty
        else { return nil }
        self.appKey = appKey
        self.accessKey = accessKey
        let raw = credentials["resourceId"] ?? Self.resourceIdAuto
        if raw == Self.resourceIdAuto || raw.isEmpty {
            // Use resolved value from auto-detect, or default to seed
            self.resourceId = credentials["resolvedResourceId"]?.isEmpty == false
                ? credentials["resolvedResourceId"]!
                : Self.resourceIdSeedASR
        } else {
            self.resourceId = raw
        }
        self.uid = ASRIdentityStore.loadOrCreateUID()
    }

    func toCredentials() -> [String: String] {
        ["appKey": appKey, "accessKey": accessKey, "resourceId": resourceId]
    }

    var isValid: Bool {
        !appKey.isEmpty && !accessKey.isEmpty
    }
}
