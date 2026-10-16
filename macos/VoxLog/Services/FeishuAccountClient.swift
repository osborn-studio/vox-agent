import AppKit
import AuthenticationServices
import Foundation

struct FeishuAccountProfile: Codable, Sendable {
    let name: String
    let tenantKey: String
    let openId: String
    let appId: String?
    let userId: String?
    let orgId: String?
}

private enum FeishuAccountError: Error { case rejected, unavailable, notConfigured, browserUnavailable }

private struct FeishuLoginStart: Decodable {
    let requestId: String
    let pollSecret: String
    let browserURL: URL
}
private struct FeishuLoginPoll: Decodable {
    let status: String
    let profile: FeishuAccountProfile?
}
private struct FeishuLoginResult: Decodable {
    let token: String?
    let profile: FeishuAccountProfile
    let targetIds: [String]
    var refreshToken: String?
    var expiresIn: Double?
    var expiresAt: Double?
    var refreshExpiresAt: Double?
}

// Normal effect injection keeps the production state machine testable without
// opening a browser, touching Keychain, or mutating the process login session.
@MainActor
struct FeishuAccountEffects {
    var send: (URLRequest) async throws -> (Data, Int)
    var load: () async -> String?
    var save: (String) async throws -> Void
    var delete: () async -> Bool
    var pauseBackup: () -> Void
    var resetLogin: () -> Void
    var completeLogin: (FeishuAccountProfile) -> Void
    var persistEndpoint: (String) -> Void
    var browser: (URL, @escaping @MainActor (URL?) -> Void) -> (() -> Void)?
    var isSignoutSuppressed: () -> Bool = { false }
    var setSignoutSuppressed: (Bool) -> Void = { _ in }

    static func live(pauseBackup: @escaping () -> Void) -> Self {
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 20
        let http = URLSession(configuration: config, delegate: AccountRedirectPolicy(), delegateQueue: nil)
        let presentation = FeishuAuthPresentation()
        return Self(send: { req in
            let (bytes, response) = try await http.bytes(for: req)
            guard let response = response as? HTTPURLResponse else { throw InputBackupError.credential }
            var data = Data()
            for try await byte in bytes {
                guard data.count < 1_048_576, !Task.isCancelled else { throw InputBackupError.credential }
                data.append(byte)
            }
            return (data, response.statusCode)
        }, load: {
            await Task.detached { KeychainService.load(key: InputBackupClient.credentialKey) }.value
        }, save: { value in
            try await Task.detached { try KeychainService.save(key: InputBackupClient.credentialKey, value: value) }.value
        }, delete: {
            await Task.detached { KeychainService.delete(key: InputBackupClient.credentialKey) }.value
        }, pauseBackup: pauseBackup, resetLogin: { LoginSession.resetLogin() }, completeLogin: { profile in
            LoginSession.completeFeishuLogin(orgID: profile.orgId ?? "", name: profile.name)
        }, persistEndpoint: { UserDefaults.standard.set($0, forKey: "vox_feishu_login_endpoint") }, browser: { url, callback in
            let auth = ASWebAuthenticationSession(url: url, callbackURLScheme: "com.osborn.voxagent.auth") { url, error in
                Task { @MainActor in callback(error == nil ? url : nil) }
            }
            auth.presentationContextProvider = presentation
            auth.prefersEphemeralWebBrowserSession = true
            guard auth.start() else { return nil }
            return { auth.cancel() }
        }, isSignoutSuppressed: { UserDefaults.standard.bool(forKey: "vox_feishu_signed_out") },
           setSignoutSuppressed: { UserDefaults.standard.set($0, forKey: "vox_feishu_signed_out") })
    }
}

@MainActor @Observable
final class FeishuAccountClient: FeishuConversationTransport, FeishuSendingTransport {
    var endpoint: String {
        didSet {
            if canonical(endpoint) != canonical(oldValue) {
                invalidate(.signedOut)
            }
        }
    }
    let timeline = FeishuTimelineModel()
    private let conversations: FeishuConversationStore
    private let outbox: FeishuConversationOutbox?
    private let effects: FeishuAccountEffects
    // All credential effects, including compensating stale-save cleanup, run in order.
    private var credentialTail: Task<Void, Never>?
    private var mutations = 0
    private var signingOut = false
    private var cleanupRequired = false
    var needsCredentialCleanup: Bool { cleanupRequired }
    var loginBlocked: Bool { mutations > 0 || signingOut || cleanupRequired }
    private var sessionEpoch = 0
    private var operation: UUID?
    private var attemptOrigin: URL?

    init(conversations: FeishuConversationStore, outbox: FeishuConversationOutbox? = nil,
         endpoint: String = FeishuAccountClient.defaultEndpoint(),
         effects: FeishuAccountEffects) {
        self.conversations = conversations
        self.outbox = outbox
        self.endpoint = endpoint
        self.effects = effects
    }

    private func canonical(_ value: String) -> URL? {
        try? InputBackupConfiguration(endpoint: value, targetID: "login").endpoint
    }

    private func credential<T>(_ body: @escaping () async throws -> T) async throws -> T {
        let previous = credentialTail
        let task = Task { @MainActor in
            await previous?.value
            return try await body()
        }
        credentialTail = Task { _ = try? await task.value }
        return try await task.value
    }

    private func startOperation() -> UUID {
        let id = UUID(); operation = id; busy = true; return id
    }
    private func finishOperation(_ id: UUID) {
        if operation == id { operation = nil; busy = false }
    }
    private func resetAttempt() {
        generation += 1
        let cancelBrowser = authentication
        authentication = nil
        attempt = nil; attemptOrigin = nil; candidate = nil; completion = nil
        operation = nil; busy = false
        cancelBrowser?()
    }
    private func invalidate(_ reason: FeishuDiscoveryFailure) {
        timeline.invalidate()
        sessionEpoch += 1
        renewal?.cancel(); renewal = nil; renewalID = nil
        retryRenewalAfter = nil
        savedEnvelope = nil; refreshCredential = nil; accessExpiresAt = nil
        session = nil; profile = nil; targetIDs = []; origin = nil
        resetAttempt()
        conversations.invalidate(reason)
        outbox?.bind(nil)
        effects.pauseBackup()
        effects.resetLogin()
    }
    private func bindDiscovery() {
        timeline.bind(conversationIdentity)
        outbox?.bind(conversationIdentity)
        if conversationIdentity == nil { conversations.invalidate(.unsupported) }
        else {
            let wasLoading = conversations.loading
            conversations.bind(conversationIdentity)
            if wasLoading { conversations.refresh(using: self) }
        }
    }
    private func removeCredential() async {
        let deleted = (try? await credential { await self.effects.delete() }) ?? false
        cleanupRequired = !deleted
    }
    private func expireSession() async {
        invalidate(.expired)
        status = L("飞书登录已撤销或失效，请重新登录。", "Feishu sign-in was revoked or expired. Please sign in again.")
        mutations += 1
        defer { mutations -= 1 }
        await removeCredential()
    }

    var conversationIdentity: FeishuConversationIdentity? {
        guard session != nil, let base = origin, let profile,
              let appId = profile.appId, !appId.isEmpty, let userId = profile.userId, !userId.isEmpty else { return nil }
        return FeishuConversationIdentity(origin: base.absoluteString, appId: appId,
                                          tenantKey: profile.tenantKey, userId: userId)
    }

    func discoverConversations(for identity: FeishuConversationIdentity) async throws -> FeishuDiscoveryResponse {
        guard await ensureSession() else { throw FeishuDiscoveryFailure.unavailable }
        guard identity == conversationIdentity, let base = origin, let token = session else {
            throw FeishuDiscoveryFailure.signedOut
        }
        let active = sessionEpoch
        var req = URLRequest(url: base.appendingPathComponent("v1/conversations"), cachePolicy: .reloadIgnoringLocalCacheData)
        req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        req.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        let (data, response) = try await effects.send(req)
        guard active == sessionEpoch, identity == conversationIdentity, !Task.isCancelled else {
            throw CancellationError()
        }
        switch response {
        case 401:
            await handleAccessRejection(token)
            throw profile == nil ? FeishuDiscoveryFailure.expired : FeishuDiscoveryFailure.unavailable
        case 403: throw FeishuDiscoveryFailure.permission
        case 404: throw FeishuDiscoveryFailure.disabled
        case 503:
            // Static server category, never show upstream bodies to the UI.
            if let payload = try? JSONSerialization.jsonObject(with: data) as? [String: String],
               payload["detail"] == "CONVERSATIONS_DISABLED" { throw FeishuDiscoveryFailure.disabled }
            throw FeishuDiscoveryFailure.unavailable
        case 200: break
        default: throw FeishuDiscoveryFailure.unavailable
        }
        guard data.count <= 1_048_576,
              let result = try? JSONDecoder().decode(FeishuDiscoveryResponse.self, from: data),
              result.identity.appId == identity.appId, result.identity.tenantKey == identity.tenantKey,
              result.identity.userId == identity.userId,
              result.conversations.count <= 2001,
              result.conversations.first?.kind == "personal",
              result.conversations.filter({ $0.kind == "personal" }).count == 1,
              Set(result.conversations.map(\.id)).count == result.conversations.count,
              result.conversations.allSatisfy({ $0.id.hasPrefix("fc_") && !$0.name.isEmpty &&
                  ["personal", "group"].contains($0.kind) && ["discovery_only", "text_send"].contains($0.capability) }) else {
            throw FeishuDiscoveryFailure.invalid
        }
        return result
    }
    func readMessages(conversation: String, cursor: String?, for identity: FeishuConversationIdentity, accept: () -> Bool = { true }) async throws -> FeishuReadPage {
        guard await ensureSession() else { throw FeishuReadingFailure.unavailable }
        guard identity == conversationIdentity, let base = origin, let token = session else { throw CancellationError() }
        guard conversation.range(of: "^fc_[A-Za-z0-9_-]{1,128}$", options: .regularExpression) != nil,
              cursor.map({ $0.range(of: "^[A-Za-z0-9_-]{32,64}$", options: .regularExpression) != nil }) ?? true else {
            throw FeishuReadingFailure.invalid
        }
        let epoch = sessionEpoch
        var parts = URLComponents(url: base.appendingPathComponent("v1/conversations/" + conversation + "/messages"), resolvingAgainstBaseURL: false)!
        parts.queryItems = [URLQueryItem(name: "pageSize", value: "50")]
        if let cursor { parts.queryItems!.append(URLQueryItem(name: "cursor", value: cursor)) }
        var req = URLRequest(url: parts.url!, cachePolicy: .reloadIgnoringLocalCacheData)
        req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        req.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        let (data, status) = try await effects.send(req)
        guard epoch == sessionEpoch, identity == conversationIdentity, !Task.isCancelled, accept() else { throw CancellationError() }
        if status == 401 { await handleAccessRejection(token); throw FeishuReadingFailure.unavailable }
        if status == 403 {
            let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            if payload?["detail"] as? String == "FEISHU_PRINCIPAL_REQUIRED" {
                await handleAccessRejection(token); throw FeishuReadingFailure.unavailable
            }
            timeline.forbidden(conversation)
            outbox?.revoke(conversation)
            if !conversations.forbiddenConversations.contains(conversation) { conversations.conversationForbidden(conversation) }
            throw FeishuReadingFailure.permission
        }
        if status == 409 { throw FeishuReadingFailure.reset }
        if status == 429 { throw FeishuReadingFailure.limit }
        guard status == 200 else { throw FeishuReadingFailure.unavailable }
        guard data.count <= 1_048_576,
              let page = try? JSONDecoder().decode(FeishuReadPage.self, from: data), page.validates(conversation) else {
            throw FeishuReadingFailure.invalid
        }
        return page
    }

    func submitText(_ record: FeishuPendingText, for identity: FeishuConversationIdentity) async throws -> FeishuSendReceipt {
        try await conversationRequest(record, identity: identity, submit: true)
    }

    func textReceipt(_ record: FeishuPendingText, for identity: FeishuConversationIdentity) async throws -> FeishuSendReceipt {
        try await conversationRequest(record, identity: identity, submit: false)
    }

    private func conversationRequest(_ record: FeishuPendingText, identity: FeishuConversationIdentity, submit: Bool) async throws -> FeishuSendReceipt {
        guard await ensureSession() else { throw FeishuSendingFailure.unavailable }
        guard identity == conversationIdentity, let base = origin, let token = session else { throw FeishuSendingFailure.signedOut }
        guard record.conversationId.range(of: "^fc_[A-Za-z0-9_-]{1,128}$", options: .regularExpression) != nil,
              record.eventId.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) != nil else { throw FeishuSendingFailure.invalid }
        let epoch = sessionEpoch
        let path = "v1/conversations/" + record.conversationId + (submit ? "/messages" : "/receipts/" + record.eventId)
        var req = URLRequest(url: base.appendingPathComponent(path), cachePolicy: .reloadIgnoringLocalCacheData)
        req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        req.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        if submit {
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode(["eventId": record.eventId, "text": record.text])
        }
        let result: (Data, Int)
        do { result = try await effects.send(req) }
        catch { throw FeishuSendingFailure.unavailable }
        guard epoch == sessionEpoch, identity == conversationIdentity, !Task.isCancelled else { throw CancellationError() }
        let (data, status) = result
        if status == 401 { await handleAccessRejection(token); throw FeishuSendingFailure.unavailable }
        if status == 403 {
            let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            if payload?["detail"] as? String == "FEISHU_PRINCIPAL_REQUIRED" {
                await handleAccessRejection(token); throw FeishuSendingFailure.unavailable
            }
            throw FeishuSendingFailure.permission
        }
        if status == 409 { throw FeishuSendingFailure.conflict }
        if status == 413 || status == 422 { throw FeishuSendingFailure.rejected }
        if status == 404 && submit { throw FeishuSendingFailure.unsupported }
        guard status == (submit ? 202 : 200), data.count <= 1_048_576,
              let receipt = try? JSONDecoder().decode(FeishuSendReceipt.self, from: data) else {
            throw FeishuSendingFailure.unavailable
        }
        guard receipt.validates(record) else { throw FeishuSendingFailure.invalid }
        return receipt
    }

    private(set) var profile: FeishuAccountProfile?
    private(set) var candidate: FeishuAccountProfile?
    private(set) var targetIDs: [String] = []
    private(set) var busy = false
    private(set) var status = ""
    private var attempt: FeishuLoginStart?
    private var origin: URL?
    private var generation = 0
    private var authentication: (() -> Void)?
    private var completion: String?
    private var session: String?
    private var savedEnvelope: String?
    private var refreshCredential: String?
    private var accessExpiresAt: Date?
    private var renewal: Task<Bool, Never>?
    private var renewalID: UUID?
    private var retryRenewalAfter: Date?
    nonisolated static let teamEndpoint = InputBackupConfiguration.teamEndpoint

    nonisolated static func defaultEndpoint(saved: String? = UserDefaults.standard.string(forKey: "vox_feishu_login_endpoint"),
                                bundled: String? = Bundle.main.object(forInfoDictionaryKey: "VoxTeamLoginURL") as? String) -> String {
        // Default new installations to the team service. Preserve every explicit saved
        // origin so existing credentials and queued records are not silently migrated.
        if let saved, let config = try? InputBackupConfiguration(endpoint: saved, targetID: "login") {
            return config.endpoint.absoluteString
        }
        if let bundled, let config = try? InputBackupConfiguration(endpoint: bundled, targetID: "login"), config.endpoint.scheme == "https" { return config.endpoint.absoluteString }
        return teamEndpoint
    }

    private static func identityKey(_ profile: FeishuAccountProfile) throws -> String {
        guard let app = profile.appId, let user = profile.userId, let org = profile.orgId,
              !app.isEmpty, !user.isEmpty, !org.isEmpty, !profile.tenantKey.isEmpty else { throw InputBackupError.credential }
        return String(decoding: try JSONEncoder().encode([app, profile.tenantKey, org, user]), as: UTF8.self)
    }

    private func envelope(_ result: FeishuLoginResult, token: String, base: URL, previous: String?) async throws -> String {
        var values = ["token": token, "origin": base.absoluteString]
        // Older servers remain readable, but cannot grant renewable credentials.
        if result.profile.userId != nil, result.profile.appId != nil, result.profile.orgId != nil {
            values["scopeIdentity"] = try Self.identityKey(result.profile)
        }
        if let refresh = result.refreshToken, let expiry = result.expiresAt, let idle = result.refreshExpiresAt {
            guard refresh.hasPrefix("vox_refresh_"), refresh.count <= 256, expiry.isFinite, idle.isFinite,
                  let lifetime = result.expiresIn, lifetime > 0, lifetime <= 3600, idle > expiry else { throw InputBackupError.credential }
            values["refreshToken"] = refresh
            values["expiresAt"] = String(expiry)
            values["expiresIn"] = String(lifetime)
            values["refreshExpiresAt"] = String(idle)
        }
        if let previous, let old = try? JSONDecoder().decode([String: String].self, from: Data(previous.utf8)),
           old["origin"] == base.absoluteString {
            if let scope = values["scopeIdentity"], old["scopeIdentity"] == scope {
                values["legacyToken"] = old["legacyToken"]
            } else if values["scopeIdentity"] != nil, old["scopeIdentity"] == nil, let prior = old["token"], prior.hasPrefix("vox_sso_") {
                // Server ownership proof permits even expired old sessions to keep
                // their queued records, without granting old sessions new access.
                if let data = try? await request("link-previous", base: base, token: token, body: ["previousToken": prior]),
                   let proof = try? JSONDecoder().decode([String: Bool].self, from: data), proof["sameIdentity"] == true {
                    values["legacyToken"] = prior
                }
            }
        }
        return String(decoding: try JSONEncoder().encode(values), as: UTF8.self)
    }

    private func remember(_ raw: String, result: FeishuLoginResult) {
        retryRenewalAfter = nil
        savedEnvelope = raw
        refreshCredential = result.refreshToken
        accessExpiresAt = result.expiresIn.map { Date().addingTimeInterval(max(0, min($0, 3600)) - 30) }
    }

    /// Single flight across timeline, backup, wake-up and periodic maintenance.
    func ensureSession() async -> Bool {
        guard !signingOut, !cleanupRequired else { return false }
        if let renewal { return await renewal.value }
        guard session != nil else { return false }
        guard refreshCredential != nil else { return true }
        if let expiry = accessExpiresAt, expiry.timeIntervalSinceNow > 300 { return true }
        if let retryRenewalAfter, retryRenewalAfter > Date() { return accessExpiresAt.map { $0 > Date() } ?? false }
        guard mutations == 0 else { return false }
        let id = UUID(); renewalID = id
        let task = Task { [weak self] in await self?.renewSession() ?? false }
        renewal = task
        let result = await task.value
        if renewalID == id { renewal = nil; renewalID = nil }
        return result
    }

    private func renewSession() async -> Bool {
        guard let refresh = refreshCredential, let base = origin, let previous = savedEnvelope, let profile else { return false }
        let epoch = sessionEpoch
        mutations += 1
        defer { mutations -= 1 }
        do {
            let data = try await request("renew", base: base, token: refresh)
            guard epoch == sessionEpoch, !Task.isCancelled else { return false }
            let result = try JSONDecoder().decode(FeishuLoginResult.self, from: data)
            guard try Self.identityKey(result.profile) == Self.identityKey(profile),
                  let token = result.token, token.hasPrefix("vox_sso_"), result.refreshToken != nil else { throw FeishuAccountError.rejected }
            let raw = try await envelope(result, token: token, base: base, previous: previous)
            guard epoch == sessionEpoch, !Task.isCancelled else { return false }
            try await credential { try await self.effects.save(raw) }
            guard epoch == sessionEpoch, !Task.isCancelled else { await removeCredential(); return false }
            session = token; targetIDs = result.targetIds
            remember(raw, result: result)
            status = L("飞书登录已自动续期。", "Feishu sign-in renewed automatically.")
            return true
        } catch {
            guard epoch == sessionEpoch, !Task.isCancelled else { return false }
            if case FeishuAccountError.rejected = error { await expireSession(); return false }
            retryRenewalAfter = Date().addingTimeInterval(60)
            status = L("登录续期暂不可用，凭据与待发送记录已保留，将自动重试。", "Renewal unavailable. Credentials and pending records retained; will retry.")
            return accessExpiresAt.map { $0 > Date() } ?? false
        }
    }

    private func handleAccessRejection(_ token: String) async {
        guard token == session else { return }
        guard refreshCredential != nil else { await expireSession(); return }
        accessExpiresAt = .distantPast
        _ = await ensureSession()
    }

    func savePersonalCredential(_ raw: String) async throws {
        guard profile == nil, !busy, mutations == 0, !signingOut, !cleanupRequired else { throw InputBackupError.credential }
        mutations += 1
        let epoch = sessionEpoch
        defer { mutations -= 1 }
        try await credential { try await self.effects.save(raw) }
        guard epoch == sessionEpoch, !Task.isCancelled else {
            await removeCredential()
            throw InputBackupError.credential
        }
    }

    func maintainSession() async {
        guard !effects.isSignoutSuppressed(), !signingOut, !cleanupRequired, candidate == nil, !busy else { return }
        if profile == nil { await restore() } else { _ = await ensureSession() }
    }


    private func baseURL() throws -> URL {
        try InputBackupConfiguration(endpoint: endpoint, targetID: "login").endpoint
    }

    private func request(_ path: String, base: URL, token: String? = nil, body: [String: String]? = nil, method: String = "POST") async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent("v1/auth/feishu/" + path))
        req.httpMethod = method
        if let token { req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode(body)
        }
        let (data, response) = try await effects.send(req)
        if response == 401 { throw FeishuAccountError.rejected }
        if response == 503, let detail = try? JSONDecoder().decode([String: String].self, from: data), detail["detail"] == "FEISHU_LOGIN_NOT_CONFIGURED" { throw FeishuAccountError.notConfigured }
        guard data.count <= 1_048_576, response == 200 else {
            throw InputBackupError.credential
        }
        return data
    }

    func begin() async {
        guard mutations == 0, !signingOut, !cleanupRequired else { return }
        resetAttempt()
        let op = startOperation()
        let active = generation
        do {
            let base = try baseURL()
            let data = try await request("begin", base: base)
            guard active == generation, !Task.isCancelled else { finishOperation(op); return }
            let start = try JSONDecoder().decode(FeishuLoginStart.self, from: data)
            guard start.browserURL.scheme == base.scheme, start.browserURL.host == base.host,
                  start.browserURL.port == base.port,
                  start.browserURL.path == "/v1/auth/feishu/browser" else { throw InputBackupError.configuration }
            attemptOrigin = base
            attempt = start
            status = L("请用飞书扫码或确认登录。", "Scan with Feishu or confirm sign-in.")
            authentication = effects.browser(start.browserURL) { [weak self] callback in
                Task { @MainActor in
                    guard let self, active == self.generation else { return }
                    guard let callback else {
                        self.resetAttempt()
                        self.status = L("登录已取消或未完成。", "Login cancelled or incomplete.")
                        return
                    }
                    await self.completeBrowser(callback, start: start, base: base, generation: active)
                    self.finishOperation(op)
                }
            }
            guard authentication != nil else { throw FeishuAccountError.browserUnavailable }
        } catch {
            guard active == generation else { return }
            finishOperation(op)
            if endpoint.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                status = L("尚未配置团队登录服务。请向管理员获取 HTTPS 地址；本机地址不能供其他 Mac 登录。", "Team sign-in URL is missing. Ask your administrator for the HTTPS URL; a local address does not serve other Macs.")
            } else if case FeishuAccountError.browserUnavailable = error {
                status = L("系统未能打开飞书认证窗口，请关闭后重试。", "macOS could not open the sign-in window. Close and retry.")
            } else if case FeishuAccountError.notConfigured = error {
                status = L("服务器尚未配置飞书登录，请联系管理员。", "Feishu sign-in is not configured on the server. Contact your administrator.")
            } else if canonical(endpoint)?.host.map({ ["127.0.0.1", "::1", "[::1]"].contains($0) }) == true {
                status = L("未能连接本机登录服务。团队使用请填写云端 HTTPS 地址。", "Could not connect to local sign-in. For team use, enter the cloud HTTPS URL.")
            } else {
                status = L("无法连接团队登录服务，请检查 HTTPS 地址和网络后重试。", "Cannot reach team sign-in. Check the HTTPS URL and network, then retry.")
            }
        }
    }

    private func completeBrowser(_ url: URL, start: FeishuLoginStart, base: URL, generation active: Int) async {
        do {
            guard url.scheme == "com.osborn.voxagent.auth", url.host == "login-complete",
                  let parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { throw InputBackupError.credential }
            let items = parts.queryItems ?? []
            guard items.filter({ $0.name == "requestId" }).count == 1,
                  items.first(where: { $0.name == "requestId" })?.value == start.requestId,
                  items.filter({ $0.name == "completion" }).count == 1,
                  let value = items.first(where: { $0.name == "completion" })?.value else { throw InputBackupError.credential }
            let data = try await request("poll", base: base, token: start.pollSecret,
                                         body: ["requestId": start.requestId, "completion": value])
            let result = try JSONDecoder().decode(FeishuLoginPoll.self, from: data)
            guard active == generation, result.status == "ready" else { throw InputBackupError.credential }
            completion = value
            candidate = result.profile
            busy = false
            status = L("请核对并确认下面的飞书账户。", "Confirm the Feishu account below.")
        } catch {
            guard active == generation else { return }
            busy = false
            status = L("登录未完成或已过期，请重试。", "Login failed or expired. Please retry.")
        }
    }

    func confirm(backup: InputBackupClient) async {
        guard mutations == 0, !signingOut, !cleanupRequired, !busy,
              let attempt, let base = attemptOrigin, base == canonical(endpoint),
              let completion, let candidate else { return }
        timeline.invalidate()
        sessionEpoch += 1
        mutations += 1
        let op = startOperation()
        let active = generation
        let epoch = sessionEpoch
        defer { mutations -= 1; finishOperation(op) }
        do {
            let data = try await request("redeem", base: base, token: attempt.pollSecret,
                                         body: ["requestId": attempt.requestId, "completion": completion])
            guard active == generation, epoch == sessionEpoch, !Task.isCancelled else { return }
            let result = try JSONDecoder().decode(FeishuLoginResult.self, from: data)
            guard let token = result.token, token.hasPrefix("vox_sso_"),
                  result.profile.openId == candidate.openId,
                  result.profile.tenantKey == candidate.tenantKey,
                  result.profile.appId == candidate.appId,
                  base == canonical(endpoint) else { throw InputBackupError.credential }
            let previous = try await credential { await self.effects.load() }
            let stored = try await envelope(result, token: token, base: base, previous: previous)
            guard active == generation, epoch == sessionEpoch, !Task.isCancelled else { return }
            let before = previous.flatMap { try? JSONDecoder().decode([String: String].self, from: Data($0.utf8)) }
            let after = try JSONDecoder().decode([String: String].self, from: Data(stored.utf8))
            let sameOwner = before?["origin"] == base.absoluteString &&
                ((after["scopeIdentity"] != nil && before?["scopeIdentity"] == after["scopeIdentity"]) || after["legacyToken"] != nil)
            if !sameOwner { try backup.configure(nil); effects.pauseBackup() }
            // A write can finish after cancellation; no new login may begin until
            // its compensating cleanup finishes (mutations remains held).
            do { try await credential { try await self.effects.save(stored) } }
            catch { await removeCredential(); throw InputBackupError.credential }
            guard active == generation, epoch == sessionEpoch, !Task.isCancelled else {
                await removeCredential()
                return
            }
            sessionEpoch += 1
            session = token; origin = base; targetIDs = result.targetIds; profile = result.profile
            remember(stored, result: result)
            bindDiscovery()
            resetAttempt()
            effects.persistEndpoint(base.absoluteString)
            effects.setSignoutSuppressed(false)
            effects.completeLogin(result.profile)
            status = L("已登录并绑定飞书。可在下方启用输入备份。", "Signed in and linked to Feishu. Enable input backup below when ready.")
        } catch {
            guard active == generation, epoch == sessionEpoch else { return }
            status = L("登录未保存，请重新扫码；已有记录保留。", "Login could not be saved. Sign in again; existing records are retained.")
        }
    }

    func restore() async {
        guard !effects.isSignoutSuppressed(), profile == nil, !busy, mutations == 0, !signingOut, !cleanupRequired else { return }
        mutations += 1
        let active = sessionEpoch
        let op = startOperation()
        defer { mutations -= 1; finishOperation(op) }
        do {
            let base = try baseURL()
            let raw = try await credential { await self.effects.load() }
            guard active == sessionEpoch, operation == op, !Task.isCancelled, let raw,
                  let token = InputBackupClient.credentialToken(raw, for: base), token.hasPrefix("vox_sso_") else { return }
            let stored = try? JSONDecoder().decode([String: String].self, from: Data(raw.utf8))
            let refresh = stored?["refreshToken"]
            let data: Data
            var renewed = false
            do {
                data = try await request(refresh == nil ? "me" : "renew", base: base, token: refresh ?? token, method: refresh == nil ? "GET" : "POST")
                renewed = refresh != nil
            } catch {
                if case FeishuAccountError.rejected = error { throw error }
                guard refresh != nil else { throw error }
                // Server, rather than the Mac's wall clock, decides if access still works.
                do { data = try await request("me", base: base, token: token, method: "GET") }
                catch { throw FeishuAccountError.unavailable }
            }
            guard active == sessionEpoch, operation == op, !Task.isCancelled else { return }
            var result = try JSONDecoder().decode(FeishuLoginResult.self, from: data)
            if refresh != nil && !renewed {
                result.refreshToken = refresh
                result.refreshExpiresAt = stored?["refreshExpiresAt"].flatMap(Double.init)
            }
            let access = result.token ?? token
            if let expected = stored?["scopeIdentity"], try Self.identityKey(result.profile) != expected { throw FeishuAccountError.rejected }
            let updated: String
            if renewed {
                guard result.token?.hasPrefix("vox_sso_") == true, result.refreshToken != nil else { throw FeishuAccountError.rejected }
                updated = try await envelope(result, token: access, base: base, previous: raw)
                guard active == sessionEpoch, operation == op, !Task.isCancelled else { return }
                try await credential { try await self.effects.save(updated) }
                guard active == sessionEpoch, operation == op, !Task.isCancelled else { await removeCredential(); return }
            } else { updated = raw }
            sessionEpoch += 1
            session = access; origin = base; profile = result.profile; targetIDs = result.targetIds
            remember(updated, result: result)
            bindDiscovery()
        } catch {
            guard active == sessionEpoch, operation == op, !Task.isCancelled else { return }
            if case FeishuAccountError.rejected = error { await expireSession() }
            status = (error is FeishuAccountError && { if case FeishuAccountError.rejected = error { return true }; return false }())
                ? L("飞书登录已失效，请重新登录。", "Feishu sign-in expired. Please sign in again.")
                : L("暂时无法恢复登录，凭据已保留，将自动重试。", "Sign-in temporarily unavailable. Credentials retained; will retry.")
        }
    }

    func logout(backup: InputBackupClient) async {
        guard !signingOut else { return }
        signingOut = true
        effects.setSignoutSuppressed(true)
        mutations += 1
        var oldToken = refreshCredential ?? session
        var base = origin
        var legacyToken = savedEnvelope.flatMap { try? JSONDecoder().decode([String: String].self, from: Data($0.utf8)) }?["legacyToken"]
        invalidate(.signedOut)
        let epoch = sessionEpoch
        let op = startOperation()
        defer { signingOut = false; mutations -= 1; finishOperation(op) }
        try? backup.configure(nil)
        if oldToken == nil, let raw = try? await credential({ await self.effects.load() }),
           let values = try? JSONDecoder().decode([String: String].self, from: Data(raw.utf8)),
           let address = values["origin"], let storedOrigin = canonical(address) {
            base = storedOrigin
            oldToken = values["refreshToken"] ?? InputBackupClient.credentialToken(raw, for: storedOrigin)
            legacyToken = values["legacyToken"]
        }
        await removeCredential()
        // Revocation is attempted even when local deletion fails; a retry still
        // cleans the credential, without needing to retain a bearer indefinitely.
        var revoked = true
        if let base {
            for token in Set([oldToken, legacyToken].compactMap { $0 }) {
                do { _ = try await request("logout", base: base, token: token) }
                catch { revoked = false }
            }
        }
        guard epoch == sessionEpoch else { return }
        if cleanupRequired {
            status = L("备份已暂停，但本机凭据清理失败，请重试退出。", "Backup paused, but credential removal failed. Retry sign-out.")
        } else if !revoked {
            status = L("本机已退出；服务器暂不可达，设备会话尚未撤销；请联系管理员撤销。", "Signed out locally. Server unreachable; device session was not revoked. Ask an administrator to revoke it.")
        } else {
            status = L("已退出；本机历史与待同步记录保留。", "Signed out; local history and pending records retained.")
        }
    }

    func cancel() {
        // Cancels OAuth only. Authenticated discovery has an independent epoch.
        resetAttempt()
    }

}

private final class AccountRedirectPolicy: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

@MainActor
private final class FeishuAuthPresentation: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        NSApp.keyWindow ?? NSApp.windows.first ?? ASPresentationAnchor()
    }
}
