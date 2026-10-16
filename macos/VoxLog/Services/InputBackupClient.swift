import CryptoKit
import Darwin
import Foundation
import Observation

struct InputBackupConfiguration: Codable, Equatable {
    static let teamEndpoint = "https://api.osbornvox.com"
    static let teamTargetID = "oc_0c9ba5130567117bf48f995cfda00964"

    /// Form suggestion only; the server still authorizes every destination.
    /// Never suggest this team's group for a different service origin.
    static func suggestedTarget(endpoint: String, availableTargets: [String]) -> String {
        let origin = try? InputBackupConfiguration(endpoint: endpoint, targetID: "default")
        if origin?.endpoint.absoluteString == teamEndpoint,
           availableTargets.isEmpty || availableTargets.contains(teamTargetID) {
            return teamTargetID
        }
        return availableTargets.count == 1 ? availableTargets[0] : ""
    }

    let endpoint: URL
    let targetID: String

    init(endpoint: String, targetID: String) throws {
        guard let url = URL(string: endpoint), let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil, parts.path.isEmpty || parts.path == "/",
              parts.scheme == "https" || (parts.scheme == "http" && ["127.0.0.1", "[::1]", "::1"].contains(host)),
              !targetID.isEmpty, targetID.utf8.count <= 80 else { throw InputBackupError.configuration }
        var normalized = parts
        normalized.scheme = parts.scheme?.lowercased()
        normalized.host = host.lowercased()
        normalized.path = ""
        if (normalized.scheme == "https" && normalized.port == 443) || (normalized.scheme == "http" && normalized.port == 80) {
            normalized.port = nil
        }
        guard let normalizedURL = normalized.url else { throw InputBackupError.configuration }
        self.endpoint = normalizedURL
        self.targetID = targetID
    }

    // Decoded persisted settings pass the same trust-boundary validation.
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(endpoint: values.decode(String.self, forKey: .endpoint),
                      targetID: values.decode(String.self, forKey: .targetID))
    }
}

/// An observed HTTP response, never proof about a previously ambiguous submission.
struct ContextPrivateHTTPFailure: Error {
    let status: Int
    let code: String?
    init(status: Int, data: Data) {
        self.status = status
        let known: Set<String> = ["PRIVATE_CONTEXT_CAPACITY", "PRIVATE_CONTEXT_REVISION_LIMIT", "PRIVATE_CONTEXT_REVISION_CONFLICT", "PRIVATE_CONTEXT_REQUEST_CONFLICT", "PRIVATE_CONTEXT_CONFLICT", "PRIVATE_CONTEXT_NOT_FOUND", "PRIVATE_CONTEXT_DISABLED", "PRIVATE_CONTEXT_UNAVAILABLE", "PRIVATE_CONTEXT_PROJECT_DENIED", "PRIVATE_CONTEXT_PROJECT_CHANGED", "ACCOUNT_DISABLED"]
        let object = (data.count <= 4096 ? try? JSONSerialization.jsonObject(with: data) : nil) as? [String: Any]
        let detail = object?["detail"] as? String
        code = detail.flatMap { known.contains($0) ? $0 : nil }
    }
}
enum InputBackupError: Error, LocalizedError {
    case configuration, credential, storage, limit, conflict
    var errorDescription: String? {
        switch self {
        case .configuration: return L("备份地址或目标无效（远程服务必须使用 HTTPS）", "Invalid backup destination; remote service requires HTTPS")
        case .credential: return L("请先配置个人备份凭据", "Configure your personal backup credential first")
        case .storage: return L("无法保存待同步记录，原文件已保留", "Cannot save pending backup; existing file preserved")
        case .limit: return L("备份文字或待同步队列超过限制", "Backup text or pending queue exceeds its limit")
        case .conflict: return L("备份编号已关联其他内容", "Backup event ID is already associated with different content")
        }
    }
}

/// Why locally saved records are not being sent. Shown on every pending row,
/// so "Saved locally" never sits unexplained. Never carries server text.
enum InputBackupHoldReason: Equatable {
    case signInExpired, renewalFailed, credentialMissing, targetDenied, backupDisabled
    case serverUnconfirmed(Int)
    case unreachable, queueFull, localStorage, awaitingCheck, deliveryBlocked

    var label: String {
        switch self {
        case .signInExpired:
            return L("飞书登录已失效 · 请在设置里重新登录飞书", "Feishu sign-in expired · Sign in again in Settings")
        case .renewalFailed:
            return L("飞书登录续期失败 · 请在设置里重新登录飞书", "Feishu sign-in could not be renewed · Sign in again in Settings")
        case .credentialMissing:
            return L("缺少备份凭据 · 请在设置里登录飞书或填写个人凭据", "No backup credential · Sign in to Feishu or add a personal credential in Settings")
        case .targetDenied:
            return L("备份目标未配置或未授权 · 请管理员核对目标 ID 和账号权限", "Backup destination not configured or not authorized · Ask your administrator to check it")
        case .backupDisabled:
            return L("服务端未启用投递 · 请管理员启用投递服务", "Delivery is disabled on the server · Ask your administrator to enable it")
        case .serverUnconfirmed(let code):
            return L("服务端未确认（HTTP \(code)） · 会自动重试", "Server did not confirm (HTTP \(code)) · Retries automatically")
        case .unreachable:
            return L("连不上备份服务 · 请检查网络和服务地址，会自动重试", "Cannot reach the backup service · Check the network and server address; retries automatically")
        case .queueFull:
            return L("本机回执记录已满 · 请联系管理员核对后迁移", "Local receipt log is full · Ask your administrator to reconcile it")
        case .localStorage:
            return L("本机记录写入失败 · 会自动重试", "Could not write local records · Retries automatically")
        case .awaitingCheck:
            return L("前一条的投递结果待核对 · 核对后继续发送", "An earlier input awaits a delivery check · Sending resumes after it is checked")
        case .deliveryBlocked:
            return L("前一条被服务端阻止 · 请联系管理员核对", "An earlier input was blocked by the server · Ask your administrator to check it")
        }
    }

    /// Reasons only a person can resolve (sign in, administrator action).
    var needsAction: Bool {
        switch self {
        case .serverUnconfirmed, .unreachable, .localStorage: return false
        default: return true
        }
    }
}

struct InputBackupProjectSnapshot: Codable, Equatable {
    let id: String
    let repositoryID: String
    var isValid: Bool {
        id.range(of: "^[A-Za-z0-9_-]{1,80}$", options: .regularExpression) == id.startIndex..<id.endIndex &&
        repositoryID.utf8.count <= 200 && repositoryID.range(of: "^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$", options: .regularExpression) == repositoryID.startIndex..<repositoryID.endIndex
    }
}

struct InputBackupProject: Codable, Equatable, Identifiable {
    let id: String
    let name: String
    let repositoryId: String
    var snapshot: InputBackupProjectSnapshot { InputBackupProjectSnapshot(id: id, repositoryID: repositoryId) }
    var isValid: Bool {
        snapshot.isValid && !name.isEmpty && name.unicodeScalars.count <= 120 &&
        name == name.trimmingCharacters(in: .whitespacesAndNewlines) &&
        name.unicodeScalars.allSatisfy { !CharacterSet.controlCharacters.contains($0) }
    }
}

struct ContextProjectDraft: Equatable {
    private(set) var frozen = false
    private(set) var project: InputBackupProjectSnapshot?
    private(set) var accountScope: String?
    private(set) var credentialScope: String?
    mutating func begin(project: InputBackupProjectSnapshot?, accountScope: String? = nil, credentialScope: String? = nil) {
        guard !frozen else { return }
        self.project = project
        self.accountScope = accountScope
        self.credentialScope = credentialScope
        frozen = true // Unknown attribution is also frozen, never inferred later.
    }
    mutating func reset() { self = ContextProjectDraft() }
}

private struct PendingInputBackupEnvelope: Codable {
    let version: Int
    let items: [PendingInputBackup]
}

private struct PendingInputBackup: Codable, Equatable {
    let eventID: String
    let text: String
    let scope: String
    var project: InputBackupProjectSnapshot? = nil
}

enum InputBackupDeliveryState: String, Codable {
    case local, pending, sending, delivered, unknown, blocked, rejected, conflict

    var label: String {
        switch self {
        case .local: return L("本机待发送", "Saved locally; awaiting submission")
        case .pending: return L("服务端排队中", "Queued on server")
        case .sending: return L("飞书投递中", "Delivering to Feishu")
        case .delivered: return L("已投递到飞书", "Delivered to Feishu")
        case .unknown: return L("投递结果未知，请核对；不会自动重发", "Delivery unknown; check with administrator. No automatic resend")
        case .blocked: return L("投递被阻止，请联系管理员", "Delivery blocked; contact administrator")
        case .conflict: return L("记录编号冲突，原文保留；请联系管理员", "Event conflict; original retained. Contact administrator")
        case .rejected: return L("投递被拒绝，请联系管理员", "Delivery rejected; contact administrator")
        }
    }
}

struct RetainedContextInput: Identifiable, Equatable, Codable {
    let id: String
    let text: String
    let project: InputBackupProjectSnapshot?
    let state: InputBackupDeliveryState
    var scope: String? = nil
    var reviewKey: String { (scope ?? "") + ":" + id }
}

private struct RetainedIdentityKey: Hashable { let scope: String; let eventID: String }

struct RetainedContextPage {
    let items: [RetainedContextInput]
    let snapshot: String
    let offset: Int
    let total: Int
    var nextOffset: Int? { offset + items.count < total ? offset + items.count : nil }
}

private struct InputBackupReceipt: Codable {
    let eventID: String
    let scope: String
    let textDigest: String
    var state: InputBackupDeliveryState
    var messageID: String?
    var project: InputBackupProjectSnapshot? = nil
}

private final class BackupRedirectPolicy: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

@MainActor @Observable
final class InputBackupClient {
    nonisolated static let credentialKey = "vox_input_backup_personal_token"
    private(set) var configuration: InputBackupConfiguration?
    private(set) var pendingCount = 0
    private(set) var statusText = L("备份未启用", "Backup disabled")
    private var pending: [PendingInputBackup] = []
    private var receipts: [InputBackupReceipt] = []
    private(set) var refreshing = false
    /// Why the current account's queue did not drain on the last attempt;
    /// nil before any attempt and after a pass that sent successfully.
    private(set) var holdReason: InputBackupHoldReason?
    private(set) var lastAttemptAt: Date?
    var isFlushing: Bool { flushing }
    private var receiptCursor = 0
    private var readable = true
    var ensureCredential: (() async -> Bool)?
    private var flushing = false
    private var generation = 0
    var configurationRevision: Int { generation }
    private var salt: Data?
    private let directory: URL
    private let credential: () -> String?
    private let displayCredential: () -> String?
    private let send: (URLRequest) async throws -> (Data, Int)
    private let defaults: UserDefaults?
    private let settingsKey = "vox_input_backup_configuration_v1"
    private var file: URL { directory.appendingPathComponent("pending.json") }
    private var receiptFile: URL { directory.appendingPathComponent("receipts-v1.json") }

    init(directory: URL? = nil, defaults: UserDefaults? = nil,
         credential: (() -> String?)? = nil,
         displayCredential: (() -> String?)? = nil,
         send: @escaping (URLRequest) async throws -> (Data, Int) = InputBackupClient.performRequest) {
        self.directory = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("VoxLog/InputBackup", isDirectory: true)
        self.defaults = defaults
        self.credential = credential ?? { KeychainService.load(key: InputBackupClient.credentialKey) }
        self.displayCredential = displayCredential ?? credential ?? { KeychainService.loadScalarWithoutInteraction(key: InputBackupClient.credentialKey) }
        self.send = send
        if let data = defaults?.data(forKey: settingsKey) {
            configuration = try? JSONDecoder().decode(InputBackupConfiguration.self, from: data)
        }
        do {
            let saltURL = self.directory.appendingPathComponent("scope-salt")
            if FileManager.default.fileExists(atPath: saltURL.path) {
                let stored = try Data(contentsOf: saltURL)
                guard stored.count == 32 else { throw InputBackupError.storage }
                salt = stored
            }
            if FileManager.default.fileExists(atPath: file.path) {
                guard salt != nil else { throw InputBackupError.storage }
                let data = try Data(contentsOf: file)
                guard data.count <= 4_000_000 else { throw InputBackupError.limit }
                if let envelope = try? JSONDecoder().decode(PendingInputBackupEnvelope.self, from: data) {
                    guard envelope.version == 2 else { throw InputBackupError.storage }
                    pending = envelope.items
                } else {
                    pending = try JSONDecoder().decode([PendingInputBackup].self, from: data)
                }
                guard pending.allSatisfy({ $0.project?.isValid ?? true }) else { throw InputBackupError.storage }
            }
            if FileManager.default.fileExists(atPath: receiptFile.path) {
                guard salt != nil else { throw InputBackupError.storage }
                let data = try Data(contentsOf: receiptFile)
                guard data.count <= 4_000_000 else { throw InputBackupError.limit }
                receipts = try JSONDecoder().decode([InputBackupReceipt].self, from: data)
                guard receipts.count <= 5000,
                      receipts.allSatisfy({ $0.state != .local && $0.eventID.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) == $0.eventID.startIndex..<$0.eventID.endIndex }),
                      Set(receipts.map { $0.scope + ":" + $0.eventID }).count == receipts.count else { throw InputBackupError.storage }
            }
            for index in receipts.indices where receipts[index].project == nil && receipts[index].textDigest.hasPrefix("project-v1:") {
                // Recover the expected snapshot only from the HMAC-matching local
                // original, never infer it from the current project catalog.
                if let original = pending.first(where: { $0.eventID == receipts[index].eventID && $0.scope == receipts[index].scope && $0.project != nil && digest($0.text, project: $0.project) == receipts[index].textDigest }) {
                    receipts[index].project = original.project
                }
                if ![.conflict, .rejected, .blocked].contains(receipts[index].state) { receipts[index].state = .unknown }
            }
            guard receipts.allSatisfy({ $0.project?.isValid ?? true }) else { throw InputBackupError.storage }
            pendingCount = pending.count
            if configuration != nil { statusText = L("备份已启用", "Backup enabled") }
        } catch {
            readable = false
            statusText = InputBackupError.storage.localizedDescription
        }
    }

    func configure(_ value: InputBackupConfiguration?) throws {
        let data = try value.map { try JSONEncoder().encode($0) }
        defaults?.set(data, forKey: settingsKey)
        configuration = value
        generation += 1
        holdReason = nil
        statusText = value == nil ? L("备份已暂停，待同步记录保留", "Backup paused; pending records retained") : L("备份已启用", "Backup enabled")
    }

    func enqueue(eventID: String, text: String, configuration: InputBackupConfiguration, project: InputBackupProjectSnapshot? = nil, expectedScope: String? = nil) throws {
        guard project?.isValid ?? true else { throw InputBackupError.configuration }
        guard readable else { throw InputBackupError.storage }
        guard !text.isEmpty, text.utf8.count <= 7000, eventID.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) == eventID.startIndex..<eventID.endIndex else { throw InputBackupError.limit }
        guard let snapshot = credential() else { throw InputBackupError.credential }
        let scope = try scopeKey(configuration, snapshot: snapshot)
        let legacy = try scopeKey(configuration, legacy: true, snapshot: snapshot)
        guard expectedScope == nil || expectedScope == scope || expectedScope == legacy else { throw InputBackupError.credential }
        let item = PendingInputBackup(eventID: eventID, text: text, scope: scope, project: project)
        if let receipt = receipts.first(where: { $0.eventID == eventID && ($0.scope == scope || $0.scope == legacy) }) {
            guard receipt.textDigest == digest(text, project: project) else { throw InputBackupError.conflict }
            return
        }
        if let prior = pending.first(where: { $0.eventID == eventID && ($0.scope == scope || $0.scope == legacy) }) {
            guard prior.text == item.text && prior.project == item.project else { throw InputBackupError.conflict }
            return
        }
        guard pending.count < 500, receipts.count + pending.count < 5000 else { throw InputBackupError.limit }
        try persist(pending + [item])
        statusText = L("已保存在本机，等待备份服务接收", "Saved locally; awaiting backup service")
    }

    nonisolated static func bindCredential(_ token: String, to endpoint: URL) throws -> String {
        let data = try JSONEncoder().encode(["token": token, "origin": endpoint.absoluteString])
        return String(decoding: data, as: UTF8.self)
    }

    nonisolated static func credentialToken(_ raw: String, for endpoint: URL) -> String? {
        if raw.hasPrefix("{") {
            guard let value = try? JSONDecoder().decode([String: String].self, from: Data(raw.utf8)),
                  value["origin"] == endpoint.absoluteString, let token = value["token"], !token.isEmpty else { return nil }
            return token
        }
        // Unbound legacy credentials must be explicitly re-entered or replaced by sign-in.
        return nil
    }

    private func token(for configuration: InputBackupConfiguration) -> String? {
        guard let raw = credential() else { return nil }
        return Self.credentialToken(raw, for: configuration.endpoint)
    }

    private func scopeKey(_ configuration: InputBackupConfiguration, legacy: Bool = false, snapshot: String? = nil) throws -> String {
        guard let raw = snapshot ?? credential(), let token = Self.credentialToken(raw, for: configuration.endpoint), !token.isEmpty else { throw InputBackupError.credential }
        let values = try? JSONDecoder().decode([String: String].self, from: Data(raw.utf8))
        let identity = values?["scopeIdentity"]
        let scopeValue = legacy ? (values?["legacyToken"] ?? token) : (identity ?? token)
        if salt == nil {
            try prepareDirectory()
            let fresh = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
            let saltURL = directory.appendingPathComponent("scope-salt")
            try fresh.write(to: saltURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: saltURL.path)
            salt = fresh
        }
        let data = salt! + (try JSONEncoder().encode([configuration.endpoint.absoluteString, configuration.targetID, scopeValue]))
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Action-time identity capture; never call from a rendered property.
    func attributionScope() throws -> String {
        guard readable else { throw InputBackupError.storage }
        guard let configuration else { throw InputBackupError.configuration }
        return try scopeKey(configuration)
    }
    /// Read-only display check. Existing account credential may be read, but this
    /// never initializes a salt/directory or requests credential renewal/network I/O.
    func attributionScopeForDisplay() -> String? {
        guard readable, salt != nil, let configuration else { return nil }
        let before = generation
        guard let snapshot = displayCredential() else { return nil }
        let scope = try? scopeKey(configuration, snapshot: snapshot)
        guard generation == before, self.configuration == configuration else { return nil }
        return scope
    }

    var currentScopePendingCount: Int {
        // Rendering settings must not create or mutate a salt file.
        guard salt != nil else { return 0 }
        guard let config = configuration, let scope = try? scopeKey(config) else { return 0 }
        let legacy = try? scopeKey(config, legacy: true)
        return pending.filter { $0.scope == scope || $0.scope == legacy }.count
    }

    private func prepareDirectory() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        var protected = directory
        var resources = URLResourceValues()
        resources.isExcludedFromBackup = true
        try protected.setResourceValues(resources)
    }

    private func persist(_ values: [PendingInputBackup]) throws {
        if values.contains(where: { $0.project != nil }) {
            // Older clients must fail closed rather than erase attribution on rewrite.
            try persistPrivateJSON(PendingInputBackupEnvelope(version: 2, items: values), to: file)
        } else {
            try persistPrivateJSON(values, to: file)
        }
        pending = values
        pendingCount = values.count
    }

    private func digest(_ text: String, project: InputBackupProjectSnapshot? = nil) -> String {
        let content = project.map { "\(text.utf8.count):" + text + "\($0.id.utf8.count):" + $0.id + "\($0.repositoryID.utf8.count):" + $0.repositoryID } ?? text
        let prefix = project == nil ? "" : "project-v1:"
        // Scope creation precedes every digest operation.
        return prefix + HMAC<SHA256>.authenticationCode(for: Data(content.utf8), using: SymmetricKey(data: salt!))
            .map { String(format: "%02x", $0) }.joined()
    }

    private func persistReceipts(_ values: [InputBackupReceipt]) throws {
        guard values.count <= 5000 else { throw InputBackupError.limit }
        try persistPrivateJSON(values, to: receiptFile)
        receipts = values
    }

    private func persistPrivateJSON<Value: Encodable>(_ values: Value, to destination: URL) throws {
        do {
            let data = try JSONEncoder().encode(values)
            guard data.count <= 4_000_000 else { throw InputBackupError.limit }
            try prepareDirectory()
            let staging = directory.appendingPathComponent("receipt-\(UUID().uuidString).tmp")
            guard FileManager.default.createFile(atPath: staging.path, contents: nil, attributes: [.posixPermissions: 0o600]) else { throw InputBackupError.storage }
            defer { try? FileManager.default.removeItem(at: staging) }
            let handle = try FileHandle(forWritingTo: staging)
            do {
                try handle.write(contentsOf: data)
                try handle.synchronize()
                try handle.close()
            } catch { try? handle.close(); throw error }
            let renamed = staging.path.withCString { source in
                destination.path.withCString { target in Darwin.rename(source, target) }
            }
            guard renamed == 0 else { throw InputBackupError.storage }
        } catch { throw InputBackupError.storage }
    }

    func deliveryState(eventID: String) -> InputBackupDeliveryState? {
        deliveryStates()[eventID]
    }

    /// Compute identity scopes once for an entire timeline render, not per row.
    func deliveryStates() -> [String: InputBackupDeliveryState] {
        guard readable, salt != nil, let config = configuration, let snapshot = credential(),
              let scope = try? scopeKey(config, snapshot: snapshot),
              let legacy = try? scopeKey(config, legacy: true, snapshot: snapshot) else { return [:] }
        var states: [String: InputBackupDeliveryState] = [:]
        for item in pending where item.scope == scope || item.scope == legacy { states[item.eventID] = .local }
        for receipt in receipts where receipt.scope == scope || receipt.scope == legacy { states[receipt.eventID] = receipt.state }
        return states
    }

    /// Read-only scoped paging. A changed queue invalidates the page token rather than skipping rows.
    private func retainedSnapshot() throws -> (scope: String, items: [RetainedContextInput]) {
        guard readable, salt != nil, pending.count <= 5000, receipts.count <= 5000 else { throw InputBackupError.storage }
        guard let config = configuration else { throw InputBackupError.configuration }
        guard let raw = credential() else { throw InputBackupError.credential }
        let scope = try scopeKey(config, snapshot: raw)
        let legacy = try scopeKey(config, legacy: true, snapshot: raw)
        let owned = pending.filter { $0.scope == scope || $0.scope == legacy }
        guard owned.allSatisfy({ $0.eventID.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) == $0.eventID.startIndex..<$0.eventID.endIndex }),
              Set(owned.map { RetainedIdentityKey(scope: $0.scope, eventID: $0.eventID) }).count == owned.count else { throw InputBackupError.storage }
        var index: [String: [String: InputBackupReceipt]] = [:]
        for receipt in receipts {
            guard index[receipt.scope]?[receipt.eventID] == nil else { throw InputBackupError.storage }
            index[receipt.scope, default: [:]][receipt.eventID] = receipt
        }
        let items = owned.compactMap { [self] item -> RetainedContextInput? in
            let receipt = index[item.scope]?[item.eventID]
            let state: InputBackupDeliveryState = receipt.map { $0.textDigest == self.digest(item.text, project: item.project) ? $0.state : .conflict } ?? .local
            guard [.local, .rejected, .blocked, .unknown, .conflict].contains(state) else { return nil }
            return RetainedContextInput(id: item.eventID, text: item.text, project: item.project, state: state, scope: item.scope)
        }
        return (scope, items)
    }
    func retainedInputs(limit: Int = 20) -> [RetainedContextInput] {
        Array(((try? retainedSnapshot().items) ?? []).prefix(max(0, min(limit, 20))))
    }
    func retainedPage(offset: Int = 0, snapshot expected: String? = nil) throws -> RetainedContextPage {
        let current = try retainedSnapshot()
        guard offset >= 0, offset % 20 == 0, offset == 0 || expected != nil, offset == 0 || offset < current.items.count else { throw InputBackupError.conflict }
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let token = digest("retained-review-v1|" + current.scope + "|" + String(decoding: try encoder.encode(current.items), as: UTF8.self))
        guard expected == nil || expected == token else { throw InputBackupError.conflict }
        return RetainedContextPage(items: Array(current.items.dropFirst(offset).prefix(20)), snapshot: token, offset: offset, total: current.items.count)
    }
    func canReview(_ input: RetainedContextInput) -> Bool {
        // Recheck only the selected scoped record, including its current receipt/digest.
        guard readable, salt != nil, pending.count <= 5000, receipts.count <= 5000,
              let config = configuration, let raw = credential(), let itemScope = input.scope,
              let scope = try? scopeKey(config, snapshot: raw),
              let legacy = try? scopeKey(config, legacy: true, snapshot: raw),
              itemScope == scope || itemScope == legacy,
              input.id.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) == input.id.startIndex..<input.id.endIndex else { return false }
        let candidates = pending.filter { $0.eventID == input.id && $0.scope == itemScope }
        guard candidates.count == 1, let item = candidates.first else { return false }
        let matchingReceipts = receipts.filter { $0.eventID == item.eventID && $0.scope == itemScope }
        guard matchingReceipts.count <= 1 else { return false }
        let receipt = matchingReceipts.first
        let state: InputBackupDeliveryState = receipt.map { $0.textDigest == digest(item.text, project: item.project) ? $0.state : .conflict } ?? .local
        guard [.local, .rejected, .blocked, .unknown, .conflict].contains(state) else { return false }
        return RetainedContextInput(id: item.eventID, text: item.text, project: item.project, state: state, scope: item.scope) == input
    }

    func fetchProjects() async throws -> [InputBackupProject] {
        guard readable else { throw InputBackupError.storage }
        guard let config = configuration else { throw InputBackupError.configuration }
        let activeGeneration = generation
        if let raw = credential(), Self.credentialToken(raw, for: config.endpoint)?.hasPrefix("vox_sso_") == true,
           let ensureCredential, !(await ensureCredential()) { throw InputBackupError.credential }
        guard generation == activeGeneration, let initial = credential(),
              let token = Self.credentialToken(initial, for: config.endpoint) else { throw InputBackupError.credential }
        let scope = try scopeKey(config, snapshot: initial)
        var request = URLRequest(url: config.endpoint.appendingPathComponent("v1/context-projects"))
        request.httpMethod = "GET"
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        let (data, code) = try await send(request)
        try Task.checkCancellation()
        guard generation == activeGeneration, configuration == config, let current = credential(),
              try scopeKey(config, snapshot: current) == scope else { throw InputBackupError.credential }
        struct Reply: Decodable { let version: Int; let projects: [InputBackupProject] }
        guard code == 200, data.count <= 1_000_000,
              let reply = try? JSONDecoder().decode(Reply.self, from: data), reply.version == 1,
              reply.projects.count <= 1000, reply.projects.allSatisfy({ $0.isValid }),
              Set(reply.projects.map(\.id)).count == reply.projects.count else { throw InputBackupError.configuration }
        return reply.projects
    }

    /// Explicit own-source read. Identity and origin remain pinned across renewal and I/O.
    func fetchWeeklySources(week: String, cursor: String?) async throws -> Data {
        if let cursor {
            guard !cursor.isEmpty, cursor.utf8.count <= 256,
                  cursor.utf8.allSatisfy({ (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || $0 == 45 || $0 == 95 }) else {
                throw InputBackupError.configuration
            }
        }
        guard readable else { throw InputBackupError.storage }
        guard let config = configuration else { throw InputBackupError.configuration }
        let activeGeneration = generation
        let originalScope = try scopeKey(config)
        if let raw = credential(), Self.credentialToken(raw, for: config.endpoint)?.hasPrefix("vox_sso_") == true,
           let ensureCredential, !(await ensureCredential()) { throw InputBackupError.credential }
        guard generation == activeGeneration, let initial = credential(),
              let token = Self.credentialToken(initial, for: config.endpoint) else { throw InputBackupError.credential }
        let scope = try scopeKey(config, snapshot: initial)
        guard scope == originalScope else { throw InputBackupError.credential }
        var components = URLComponents(url: config.endpoint.appendingPathComponent("v1/context-weekly-draft"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "week", value: week)]
        if let cursor { components.queryItems?.append(URLQueryItem(name: "cursor", value: cursor)) }
        guard let url = components.url else { throw InputBackupError.configuration }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        let (data, code) = try await send(request)
        try Task.checkCancellation()
        guard generation == activeGeneration, configuration == config, let current = credential(),
              try scopeKey(config, snapshot: current) == scope else { throw InputBackupError.credential }
        guard code == 200, data.count <= 4_000_000 else { throw InputBackupError.configuration }
        return data
    }

    /// Explicit private journal action; never queues, retries, or shares its body.
    func privateContextRequest(path: String, method: String, body: Data?) async throws -> Data {
        let prefix = "v1/context-private"
        let components = path.split(separator: "/", omittingEmptySubsequences: false)
        let allowed = (path == "v1/context-projects" && method == "GET" && body == nil)
            || (path == prefix + "-authorization" && method == "GET" && body == nil)
            || (path == prefix && method == "POST")
            || (path == prefix + "-search" && method == "POST")
            || (path == prefix + "-reference-check" && method == "POST")
            || (components.count == 5 && components[0] == "v1" && components[1] == "context-private-events"
                && Self.privateIdentifier(String(components[2])) && components[3] == "requests"
                && Self.privateIdentifier(String(components[4])) && method == "GET")
            || (components.count == 3 && components[0] == "v1" && components[1] == "context-private-events"
                && Self.privateIdentifier(String(components[2])) && method == "GET")
            || (components.count == 3 && components[0] == "v1" && components[1] == "context-private"
                && Self.privateIdentifier(String(components[2])) && ["GET", "PUT"].contains(method))
            || (components.count == 4 && components[0] == "v1" && components[1] == "context-private"
                && Self.privateIdentifier(String(components[2])) && components[3] == "withdraw" && method == "POST")
        guard allowed, (body?.count ?? 0) <= 131072 else { throw InputBackupError.configuration }
        guard readable else { throw InputBackupError.storage }
        guard let config = configuration else { throw InputBackupError.configuration }
        let activeGeneration = generation
        let originalScope = try scopeKey(config)
        if let raw = credential(), Self.credentialToken(raw, for: config.endpoint)?.hasPrefix("vox_sso_") == true,
           let ensureCredential, !(await ensureCredential()) { throw InputBackupError.credential }
        guard generation == activeGeneration, configuration == config, let initial = credential(),
              let token = Self.credentialToken(initial, for: config.endpoint),
              try scopeKey(config, snapshot: initial) == originalScope else { throw InputBackupError.credential }
        var request = URLRequest(url: config.endpoint.appendingPathComponent(path))
        request.httpMethod = method; request.httpBody = body
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, code) = try await send(request)
        try Task.checkCancellation()
        guard generation == activeGeneration, configuration == config, let current = credential(),
              try scopeKey(config, snapshot: current) == originalScope else { throw InputBackupError.credential }
        guard data.count <= 4_000_000 else { throw InputBackupError.configuration }
        guard code == 200 else { throw ContextPrivateHTTPFailure(status: code, data: data) }
        return data
    }

    private static func privateIdentifier(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 128
        && value.utf8.allSatisfy { (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || $0 == 45 || $0 == 95 }
    }

    /// A bounded, read-only pass. Never reposts unknown or missing deliveries.
    func refreshReceipts(configuration: InputBackupConfiguration, eventID: String? = nil) async {
        guard readable, !refreshing, salt != nil, !receipts.isEmpty else { return }
        refreshing = true
        defer { refreshing = false }
        let activeGeneration = generation
        do {
            if let raw = credential(), Self.credentialToken(raw, for: configuration.endpoint)?.hasPrefix("vox_sso_") == true,
               let ensureCredential, !(await ensureCredential()) { return }
            guard generation == activeGeneration else { return }
            guard let initial = credential() else { return }
            let scope = try scopeKey(configuration, snapshot: initial)
            let legacy = try scopeKey(configuration, legacy: true, snapshot: initial)
            let candidates = receipts.filter {
                ($0.scope == scope || $0.scope == legacy) && $0.state != .delivered && $0.state != .conflict &&
                (eventID == nil ? ($0.state == .pending || $0.state == .sending) : $0.eventID == eventID)
            }
            guard !candidates.isEmpty else { return }
            let offset = receiptCursor % candidates.count
            let count = min(20, candidates.count)
            receiptCursor = (offset + count) % candidates.count
            for index in 0..<count {
                try Task.checkCancellation()
                let receipt = candidates[(offset + index) % candidates.count]
                guard generation == activeGeneration, let raw = credential(),
                      try scopeKey(configuration, snapshot: raw) == scope,
                      let token = Self.credentialToken(raw, for: configuration.endpoint) else { return }
                var request = URLRequest(url: configuration.endpoint.appendingPathComponent("v1/input-backups").appendingPathComponent(receipt.eventID))
                request.httpMethod = "GET"
                request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                let (data, code) = try await send(request)
                guard readable, generation == activeGeneration, let current = credential(),
                      try scopeKey(configuration, snapshot: current) == scope else { return }
                guard code == 200, let value = Self.receiptReply(data, eventID: receipt.eventID, project: receipt.project, requiresProject: receipt.textDigest.hasPrefix("project-v1:")) else { continue }
                guard let position = receipts.firstIndex(where: { $0.eventID == receipt.eventID && $0.scope == receipt.scope }) else { continue }
                // A concurrent POST acknowledgement cannot regress a final receipt.
                guard receipts[position].state != .delivered, receipts[position].state != .conflict else { continue }
                var updated = receipts
                updated[position].state = value.0
                updated[position].messageID = value.1
                try persistReceipts(updated)
            }
        } catch {
            // Preserve the last confirmed receipt when offline, cancelled or storage fails.
        }
    }

    private static func receiptReply(_ data: Data, eventID: String, project: InputBackupProjectSnapshot? = nil, requiresProject: Bool = false) -> (InputBackupDeliveryState, String?)? {
        struct Attribution: Decodable { let version: Int; let projectId: String; let repositoryId: String }
        struct Reply: Decodable { let eventId: String; let status: InputBackupDeliveryState; let messageId: String?; let attribution: Attribution? }
        guard data.count <= 4096, let reply = try? JSONDecoder().decode(Reply.self, from: data),
              reply.eventId == eventID, reply.status != .local, reply.status != .conflict else { return nil }
        if let project {
            guard let echoed = reply.attribution, echoed.version == 1,
                  echoed.projectId == project.id, echoed.repositoryId == project.repositoryID else { return nil }
        } else {
            guard !requiresProject, reply.attribution == nil else { return nil }
        }
        let messageID = reply.messageId
        if let messageID, messageID.utf8.count > 132 { return nil }
        if reply.status == .delivered {
            guard let messageID, messageID.range(of: "^om_[A-Za-z0-9_-]{1,128}$", options: .regularExpression) == messageID.startIndex..<messageID.endIndex else { return nil }
        }
        return (reply.status, messageID)
    }

    func flush(configuration: InputBackupConfiguration) async {
        guard readable, !flushing, !pending.isEmpty else { return }
        flushing = true
        lastAttemptAt = Date()
        let activeGeneration = generation
        if let raw = credential(), Self.credentialToken(raw, for: configuration.endpoint)?.hasPrefix("vox_sso_") == true,
           let ensureCredential, !(await ensureCredential()) {
            holdReason = .renewalFailed
            flushing = false
            return
        }
        guard generation == activeGeneration else { flushing = false; return }
        var acknowledged: [PendingInputBackup] = []
        defer {
            if !acknowledged.isEmpty {
                do { try persist(pending.filter { !acknowledged.contains($0) }) }
                catch { statusText = L("本机确认尚未保存，记录保留待重试", "Local acknowledgement not saved; retained for retry") }
            }
            flushing = false
        }
        do {
            guard let initial = credential() else { throw InputBackupError.credential }
            let scope = try scopeKey(configuration, snapshot: initial)
            // Each pass is bounded. A future pass resumes after any error.
            let legacy = try scopeKey(configuration, legacy: true, snapshot: initial)
            let batch = pending.filter { item in
                guard item.scope == scope || item.scope == legacy else { return false }
                // Retained failures must not occupy every bounded batch slot.
                return !receipts.contains { $0.eventID == item.eventID && ($0.scope == scope || $0.scope == legacy) && ([.blocked, .rejected, .conflict, .unknown].contains($0.state)) }
            }.prefix(20)
            for item in batch {
                try Task.checkCancellation()
                guard generation == activeGeneration, let snapshot = credential(),
                      try scopeKey(configuration, snapshot: snapshot) == scope,
                      let token = Self.credentialToken(snapshot, for: configuration.endpoint) else { return }
                if let receipt = receipts.first(where: { $0.eventID == item.eventID && ($0.scope == scope || $0.scope == legacy) }) {
                    guard receipt.textDigest == digest(item.text, project: item.project) else {
                        var updated = receipts
                        if let index = updated.firstIndex(where: { $0.eventID == receipt.eventID && $0.scope == receipt.scope }) {
                            updated[index].state = .conflict
                            try persistReceipts(updated)
                        }
                        continue
                    }
                    if [.pending, .sending, .delivered].contains(receipt.state) { acknowledged.append(item) }
                    continue
                }
                guard receipts.count < 5000 else { throw InputBackupError.limit }
                var request = URLRequest(url: configuration.endpoint.appendingPathComponent("v1/input-backups"))
                request.httpMethod = "POST"
                request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                var payload = ["eventId": item.eventID, "targetId": configuration.targetID, "text": item.text]
                if let project = item.project {
                    payload["projectId"] = project.id
                    payload["repoSource"] = "manual"
                    payload["repository"] = project.repositoryID
                }
                request.httpBody = try JSONSerialization.data(withJSONObject: payload)
                let (data, code) = try await send(request)
                guard readable else { return }
                let currentScope = credential().flatMap { try? scopeKey(configuration, snapshot: $0) }
                if currentScope != scope {
                    // Record an authentic in-flight acceptance only under its original
                    // actor scope. It is invisible to the new actor and prevents repost.
                    if code == 202 {
                        let reply = Self.receiptReply(data, eventID: item.eventID, project: item.project) ?? (.unknown, nil)
                        var updated = receipts
                        updated.append(InputBackupReceipt(eventID: item.eventID, scope: item.scope,
                            textDigest: digest(item.text, project: item.project), state: reply.0, messageID: reply.1, project: item.project))
                        try persistReceipts(updated)
                    }
                    return
                }
                guard code == 202 else {
                    statusText = Self.failureMessage(data: data, statusCode: code)
                    let detail = data.count <= 4096 ? (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String : nil
                    holdReason = Self.holdReason(statusCode: code, detail: detail)
                    if [400, 413, 422, 409].contains(code) || (code == 403 && detail == "PROJECT_DENIED") {
                        // Keep the original locally; one poison item must not block
                        // unrelated inputs. Conflict could represent prior acceptance.
                        var updated = receipts
                        updated.append(InputBackupReceipt(eventID: item.eventID, scope: item.scope,
                            textDigest: digest(item.text, project: item.project), state: code == 409 ? .conflict : .rejected, messageID: nil, project: item.project))
                        try persistReceipts(updated)
                        continue
                    }
                    // Authentication, target policy and outages affect the whole pass.
                    return
                }
                // HTTP acceptance with an invalid body is ambiguous, never a reason
                // to automatically submit again. Keep it for explicit GET reconciliation.
                let parsed = Self.receiptReply(data, eventID: item.eventID, project: item.project)
                let reply = parsed ?? (.unknown, nil)
                let state = reply.0.rawValue
                var updated = receipts
                if !updated.contains(where: { $0.eventID == item.eventID && $0.scope == item.scope }) {
                    updated.append(InputBackupReceipt(eventID: item.eventID, scope: item.scope, textDigest: digest(item.text, project: item.project), state: reply.0, messageID: reply.1, project: item.project))
                    // Store the receipt before removing a locally queued input.
                    try persistReceipts(updated)
                }
                if parsed == nil {
                    statusText = InputBackupDeliveryState.unknown.label
                    holdReason = .awaitingCheck
                    return
                }
                if ["blocked", "rejected"].contains(state) {
                    statusText = L("飞书投递被阻止，原文已留在本机；请联系管理员核对", "Delivery blocked; retained locally. Contact your administrator")
                    holdReason = .deliveryBlocked
                    return
                }
                acknowledged.append(item)
                holdReason = nil
                statusText = state == "unknown"
                    ? L("服务端已保存，但飞书投递结果未知", "Server saved the input; Feishu delivery is unknown")
                    : L("备份服务已接收；飞书投递状态以服务端为准", "Backup service accepted; Feishu delivery is tracked by the server")
            }
        } catch {
            statusText = L("备份暂未完成，记录已保留", "Backup incomplete; records retained")
            holdReason = Self.holdReason(error: error)
        }
    }

    /// A refusal for the whole pass, by status and the server's short code only.
    private static func holdReason(statusCode: Int, detail: String?) -> InputBackupHoldReason {
        switch (statusCode, detail) {
        case (401, _): return .signInExpired
        case (403, "TARGET_DENIED"): return .targetDenied
        case (503, "BACKUP_DISABLED"): return .backupDisabled
        default: return .serverUnconfirmed(statusCode)
        }
    }

    private static func holdReason(error: Error) -> InputBackupHoldReason {
        switch error {
        case InputBackupError.credential: return .credentialMissing
        case InputBackupError.limit: return .queueFull
        case InputBackupError.storage: return .localStorage
        case is URLError: return .unreachable
        default: return .localStorage
        }
    }

    private static func failureMessage(data: Data, statusCode: Int) -> String {
        let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
        switch (statusCode, detail) {
        case (503, "BACKUP_DISABLED"):
            return L("云端备份未启用，记录已保留；请管理员启用投递服务", "Server backup is disabled; records retained. Ask your administrator to enable delivery")
        case (403, "TARGET_DENIED"):
            return L("备份目标未配置或未授权，记录已保留；请管理员核对目标 ID 和账号权限", "Backup destination is not configured or authorized; records retained. Ask your administrator to check the target ID and account access")
        case (401, _):
            return L("备份登录已失效，记录已保留；请重新登录飞书", "Backup sign-in is no longer valid; records retained. Sign in to Feishu again")
        default:
            return L("备份未确认，记录已保留；请检查凭据、目标或大小限制", "Backup unconfirmed; retained locally. Check credentials, target and size limit")
        }
    }

    private static func performRequest(_ request: URLRequest) async throws -> (Data, Int) {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 20
        let session = URLSession(configuration: config, delegate: BackupRedirectPolicy(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.bytes(for: request)
        var data = Data()
        for try await byte in bytes {
            guard data.count < 4_000_000 else { throw InputBackupError.limit }
            data.append(byte)
        }
        return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
    }
}
