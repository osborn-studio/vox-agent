#if FEISHU_ACCOUNT_HARNESS
// Compiled standalone by verify_feishu_account.py with production AccountClient.
import Foundation

@MainActor private func waitUntil(_ test: () -> Bool) async {
    let deadline = ContinuousClock.now + .seconds(2)
    while !test() {
        precondition(ContinuousClock.now < deadline, "async scenario timed out")
        try! await Task.sleep(for: .milliseconds(1))
    }
}

@MainActor private final class Fixture {
    var credential: String?
    var paused = 0
    var resets = 0
    var deletesFail = false
    var pending: CheckedContinuation<(Data, Int), Error>?
    var savePending: CheckedContinuation<Void, Never>?
    var holdSave = false
    var holdDelete = false
    var deletePending: CheckedContinuation<Void, Never>?
    var heldPath: String?
    var callback: (@MainActor (URL?) -> Void)?
    var requests: [String] = []
    var renewable = false
    var lifetime: Double = 3600
    var renewStatus = 200
    var renewBody: String?
    var meStatus = 200
    var suppressed = false
    var expiryOffset: Double = 0
    var discoveryStatus = 200
    var messagesPrincipalRequired = false
    var saveFails = false
    var browserFails = false
    var profile = #"{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice"}"#
    var result: String {
        let extra = renewable ? ",\"refreshToken\":\"vox_refresh_fixture\",\"expiresIn\":\(lifetime),\"expiresAt\":\(Date().timeIntervalSince1970 + lifetime + expiryOffset),\"refreshExpiresAt\":\(Date().timeIntervalSince1970 + 90 * 86400)" : ""
        return "{\"token\":\"vox_sso_fake\",\"profile\":\(profile),\"targetIds\":[]\(extra)}"
    }
    var discovery: String { #"{"identity":{"appId":"cli_test","tenantKey":"team","userId":"alice"},"conversations":[{"id":"fc_dm","kind":"personal","name":"Feishu CLI","capability":"discovery_only"}]}"# }
    func effects() -> FeishuAccountEffects {
        FeishuAccountEffects(send: { req in
            let path = req.url!.lastPathComponent
            self.requests.append(path)
            if self.heldPath == path { return try await withCheckedThrowingContinuation { self.pending = $0 } }
            switch path {
            case "begin": return (Data(#"{"requestId":"r","pollSecret":"p","browserURL":"https://fixture.test/v1/auth/feishu/browser"}"#.utf8), 200)
            case "messages" where self.messagesPrincipalRequired: return (Data(#"{"detail":"FEISHU_PRINCIPAL_REQUIRED"}"#.utf8), 403)
            case "me": return (Data(self.result.utf8), self.meStatus)
            case "renew": return (Data((self.renewBody ?? self.result).utf8), self.renewStatus)
            case "link-previous": return (Data(#"{"sameIdentity":true}"#.utf8), 200)
            case "poll": return (Data("{\"status\":\"ready\",\"profile\":\(self.profile)}".utf8), 200)
            case "conversations": return (Data(self.discovery.utf8), self.discoveryStatus)
            default: return (Data(self.result.utf8), 200)
            }
        }, load: { self.credential }, save: { value in
            if self.holdSave { await withCheckedContinuation { self.savePending = $0 } }
            if self.saveFails { throw InputBackupError.storage }
            self.credential = value
        }, delete: {
            if self.holdDelete { await withCheckedContinuation { self.deletePending = $0 } }
            if self.deletesFail { return false }
            self.credential = nil; return true
        }, pauseBackup: { self.paused += 1 }, resetLogin: { self.resets += 1 }, completeLogin: { _ in }, persistEndpoint: { _ in }, browser: { _, callback in
            if self.browserFails { return nil }
            self.callback = callback
            return { self.callback = nil }
        }, isSignoutSuppressed: { self.suppressed }, setSignoutSuppressed: { self.suppressed = $0 })
    }
    func make() throws -> (FeishuAccountClient, FeishuConversationStore) {
        let store = FeishuConversationStore()
        credential = try InputBackupClient.bindCredential("vox_sso_fake", to: URL(string: "https://fixture.test")!)
        return (FeishuAccountClient(conversations: store, endpoint: "https://fixture.test", effects: effects()), store)
    }
    func candidate(_ account: FeishuAccountClient) async {
        await account.begin()
        callback?(URL(string: "com.osborn.voxagent.auth://login-complete?requestId=r&completion=c"))
        await waitUntil { account.candidate != nil }
    }
    func finish(_ text: String, status: Int = 200) {
        pending!.resume(returning: (Data(text.utf8), status)); pending = nil
    }
}

@main struct FeishuAccountHarness {
    @MainActor static func main() async throws {
        let backup = InputBackupClient(directory: URL(fileURLWithPath: CommandLine.arguments[1]).appendingPathComponent(UUID().uuidString), credential: { fatalError("credential read") })
        // Separate OAuth attempt epoch; canonical origin edits preserve authenticated discovery.
        do {
            let f = Fixture(); let (a, store) = try f.make(); await a.restore()
            let identity = a.conversationIdentity!
            f.heldPath = "conversations"; store.refresh(using: a)
            await waitUntil { f.pending != nil }
            await a.begin(); a.cancel()
            a.endpoint = "https://FIXTURE.test:443/"
            precondition(a.conversationIdentity == identity)
            f.finish(f.discovery)
            await waitUntil { !store.loading }
            precondition(store.conversations.count == 1 && store.failure == nil)
        }
        // Candidate endpoint edit clears browser/attempt/busy and prevents redemption.
        do {
            let f = Fixture(); let (a, _) = try f.make(); await f.candidate(a)
            a.endpoint = "https://other.test"
            precondition(a.candidate == nil && !a.busy && f.callback == nil)
            await a.confirm(backup: backup)
            precondition(!f.requests.contains("redeem"))
        }
        // Late restore and late 401 cannot overwrite a changed origin.
        for status in [200, 401] {
            let f = Fixture(); let (a, _) = try f.make(); f.heldPath = "me"
            let task = Task { await a.restore() }; await waitUntil { f.pending != nil }
            a.endpoint = "https://other.test"; precondition(!a.busy)
            f.finish(f.result, status: status); await task.value
            precondition(a.profile == nil && a.conversationIdentity == nil && !a.busy)
        }
        // Stale save cleanup stays serialized; new attempts are blocked until complete.
        do {
            let f = Fixture(); let (a, _) = try f.make(); await f.candidate(a); f.holdSave = true
            let task = Task { await a.confirm(backup: backup) }; await waitUntil { f.savePending != nil }
            a.endpoint = "https://other.test"; let count = f.requests.count; await a.begin()
            precondition(f.requests.count == count)
            f.savePending!.resume(); await task.value
            precondition(f.credential == nil && a.profile == nil && !a.busy)
        }
        // Candidate/result identity must match.
        do {
            let f = Fixture(); let (a, _) = try f.make(); await f.candidate(a)
            f.profile = f.profile.replacingOccurrences(of: "ou_alice", with: "ou_mallory")
            await a.confirm(backup: backup); precondition(a.profile == nil)
        }
        // Logout invalidates immediately, failed cleanup can be retried, no new login races.
        do {
            let f = Fixture(); let (a, store) = try f.make(); await a.restore(); f.deletesFail = true; f.heldPath = "logout"
            let task = Task { await a.logout(backup: backup) }
            await waitUntil { f.pending != nil }
            precondition(a.conversationIdentity == nil && store.conversations.isEmpty && f.resets == 1 && f.paused > 0)
            let count = f.requests.count; await a.begin(); await a.restore(); precondition(count == f.requests.count)
            f.finish("{}"); await task.value
            await a.begin(); precondition(count == f.requests.count)
            precondition(a.needsCredentialCleanup)
            f.deletesFail = false; f.heldPath = nil; await a.logout(backup: backup)
            precondition(f.credential == nil && !a.busy && !a.needsCredentialCleanup)
            await a.begin(); precondition(a.busy); a.cancel(); precondition(!a.busy)
        }
        // Actual discovery 401 uses full invalidation and cleans credentials/backup/login.
        do {
            let f = Fixture(); let (a, store) = try f.make(); await a.restore(); f.heldPath = "conversations"
            store.refresh(using: a); await waitUntil { f.pending != nil }; f.finish("{}", status: 401)
            await waitUntil { a.conversationIdentity == nil && f.credential == nil }
            precondition(store.failure == .expired && f.resets == 1 && f.paused > 0)
        }
        do {
            let f = Fixture(); let (a, store) = try f.make(); f.profile = #"{"name":"Old","tenantKey":"team","openId":"ou_old"}"#
            await a.restore(); precondition(a.profile != nil && store.failure == .unsupported)
        }
        // State is cleared before a suspended Keychain deletion finishes.
        do {
            let f = Fixture(); let (a, store) = try f.make(); await a.restore(); f.holdDelete = true
            let logout = Task { await a.logout(backup: backup) }
            await waitUntil { f.deletePending != nil }
            precondition(a.profile == nil && a.conversationIdentity == nil && store.navigation.identity == nil)
            precondition(f.resets == 1 && f.paused > 0 && f.credential != nil)
            f.deletePending!.resume(); await logout.value
            precondition(f.credential == nil)
        }
        // A late discovery 401 from the previous session cannot expire a newer login.
        do {
            let f = Fixture(); let (a, _) = try f.make(); await a.restore()
            let oldIdentity = a.conversationIdentity!; f.heldPath = "conversations"
            let old = Task { try await a.discoverConversations(for: oldIdentity) }
            await waitUntil { f.pending != nil }
            await f.candidate(a); await a.confirm(backup: backup)
            let resetCount = f.resets
            f.finish("{}", status: 401)
            do { _ = try await old.value; preconditionFailure("late 401 accepted") }
            catch is CancellationError {} catch { preconditionFailure("wrong stale category") }
            precondition(a.profile != nil && f.resets == resetCount && f.credential != nil)
        }
        // Logout during a suspended save takes effect now; both ordered deletes
        // finish before a new login can write another credential.
        do {
            let f = Fixture(); let (a, _) = try f.make(); await f.candidate(a); f.holdSave = true
            let confirm = Task { await a.confirm(backup: backup) }
            await waitUntil { f.savePending != nil }
            let logout = Task { await a.logout(backup: backup) }
            await waitUntil { f.resets == 1 }
            precondition(a.profile == nil && a.candidate == nil)
            f.savePending!.resume(); await confirm.value; await logout.value
            precondition(f.credential == nil)
            f.holdSave = false; await f.candidate(a); await a.confirm(backup: backup)
            precondition(a.profile != nil && f.credential != nil)
        }
        // Cancel a restore, then retry: old work releases only its own busy token.
        do {
            let f = Fixture(); let (a, _) = try f.make(); f.heldPath = "me"
            let restore = Task { await a.restore() }; await waitUntil { f.pending != nil }
            a.cancel(); precondition(!a.busy)
            f.finish(f.result); await restore.value; precondition(a.profile == nil)
            f.heldPath = nil; await a.restore(); precondition(a.profile != nil && !a.busy)
        }
        // Late redeem after endpoint edit never writes a credential.
        do {
            let f = Fixture(); let (a, _) = try f.make(); await f.candidate(a); f.credential = nil
            f.heldPath = "redeem"
            let confirm = Task { await a.confirm(backup: backup) }; await waitUntil { f.pending != nil }
            a.endpoint = "https://other.test"; f.finish(f.result); await confirm.value
            precondition(a.profile == nil && f.credential == nil && !a.busy)
        }
        // Clean installation never silently uses a developer's loopback server.
        precondition(FeishuAccountClient.defaultEndpoint(saved: "http://127.0.0.1:8766/", bundled: nil) == "http://127.0.0.1:8766")
        precondition(FeishuAccountClient.defaultEndpoint(saved: "http://[::1]:8766", bundled: nil) == "http://[::1]:8766")
        precondition(FeishuAccountClient.defaultEndpoint(saved: "https://custom.test/", bundled: nil) == "https://custom.test")
        precondition(FeishuAccountClient.defaultEndpoint(saved: "http://127.0.0.1:9876", bundled: nil) == "http://127.0.0.1:9876")
        precondition(FeishuAccountClient.defaultEndpoint(saved: nil, bundled: nil) == "https://api.osbornvox.com")
        precondition(FeishuAccountClient.defaultEndpoint(saved: nil, bundled: "https://TEAM.test/") == "https://team.test")
        precondition(FeishuAccountClient.defaultEndpoint(saved: nil, bundled: "http://192.168.1.1") == "https://api.osbornvox.com")
        do {
            let f = Fixture(); let (a, _) = try f.make(); a.endpoint = ""
            await a.begin(); precondition(!a.busy && f.requests.isEmpty && a.status.contains("missing"))
            a.endpoint = "https://fixture.test"; f.browserFails = true
            await a.begin(); precondition(!a.busy && a.status.contains("could not open"))
        }
        // Expired access restores through refresh, retaining identity and Keychain envelope.
        do {
            let f = Fixture(); let (a, _) = try f.make()
            f.profile = #"{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice","orgId":"org"}"#
            f.renewable = true
            f.credential = String(decoding: try JSONEncoder().encode(["token":"vox_sso_expired", "refreshToken":"vox_refresh_fixture", "origin":"https://fixture.test"]), as: UTF8.self)
            await a.restore()
            precondition(a.profile != nil && f.requests.contains("renew") && !f.requests.contains("me"))
            precondition(f.credential!.contains("scopeIdentity") && f.credential!.contains("legacyToken"))
        }
        // Transient failure and Keychain save failure retain the reusable refresh credential.
        for saveFailure in [false, true] {
            let f = Fixture(); let (a, _) = try f.make()
            f.profile = #"{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice","orgId":"org"}"#
            f.renewable = true; f.renewStatus = saveFailure ? 200 : 503; f.meStatus = 503; f.saveFails = saveFailure
            f.credential = String(decoding: try JSONEncoder().encode(["token":"vox_sso_expired", "refreshToken":"vox_refresh_fixture", "origin":"https://fixture.test"]), as: UTF8.self)
            let before = f.credential
            await a.restore(); precondition(a.profile == nil && f.credential == before && !a.busy)
            f.renewStatus = 200; f.meStatus = 200; f.saveFails = false
            await a.restore(); precondition(a.profile != nil)
        }
        // Concurrent renewal callers share a request; logout defeats late renewal.
        do {
            let f = Fixture(); let (a, _) = try f.make()
            f.profile = #"{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice","orgId":"org"}"#
            f.renewable = true; f.lifetime = 100
            await f.candidate(a); await a.confirm(backup: backup)
            f.heldPath = "renew"
            let first = Task { await a.ensureSession() }
            await waitUntil { f.pending != nil }
            let second = Task { await a.ensureSession() }
            await Task.yield()
            precondition(f.requests.filter { $0 == "renew" }.count == 1)
            await a.logout(backup: backup)
            f.finish(f.result)
            let one = await first.value; let two = await second.value
            precondition(!one && !two && a.profile == nil && f.credential == nil)
        }
        // Server-relative expiry tolerates a client clock two hours ahead.
        do {
            let f = Fixture(); let (a, _) = try f.make()
            f.profile = #"{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice","orgId":"org"}"#
            f.renewable = true; f.expiryOffset = -7200
            await f.candidate(a); await a.confirm(backup: backup)
            precondition(a.profile != nil)
            let saved = f.credential
            do { try await a.savePersonalCredential("manual-override"); preconditionFailure("active account credential overwritten") }
            catch InputBackupError.credential {}
            precondition(f.credential == saved)
            let valid = await a.ensureSession(); precondition(valid && !f.requests.contains("renew"))
            f.discoveryStatus = 401
            do { _ = try await a.discoverConversations(for: a.conversationIdentity!); preconditionFailure("401 accepted") }
            catch {}
            precondition(a.profile != nil && f.credential != nil && f.requests.filter { $0 == "renew" }.count == 1)
        }
        // A persisted explicit sign-out suppresses restore in a newly created client.
        do {
            let f = Fixture(); let (a, _) = try f.make(); await a.restore()
            f.deletesFail = true
            await a.logout(backup: backup)
            let calls = f.requests.count
            let fresh = FeishuAccountClient(conversations: FeishuConversationStore(), endpoint: "https://fixture.test", effects: f.effects())
            await fresh.maintainSession(); await fresh.restore()
            precondition(f.requests.count == calls && fresh.profile == nil && f.suppressed)
        }
        // Temporary tenant configuration does not destroy a device credential.
        do {
            let f = Fixture(); let (a, _) = try f.make()
            f.profile = #"{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice","orgId":"org"}"#
            f.renewable = true
            await f.candidate(a); await a.confirm(backup: backup)
            let credential = f.credential
            f.messagesPrincipalRequired = true; f.renewStatus = 503
            for _ in 0..<2 {
                do { _ = try await a.readMessages(conversation: "fc_test", cursor: nil, for: a.conversationIdentity!) }
                catch {}
            }
            precondition(a.profile != nil && f.credential == credential)
            precondition(f.requests.filter { $0 == "renew" }.count == 1)
        }
        // Renewal failures map the server's detail code and HTTP status to fixed
        // status lines plus a last-renewal record; raw server text never reaches the UI.
        do {
            let cases: [(Int, String, FeishuRenewalOutcome, String, Bool)] = [
                (503, "MEMBERSHIP_CHECK_UNAVAILABLE", .membershipCheckUnavailable, "membership", true),
                (503, "PRIVATE UPSTREAM BODY xyz", .serverUnavailable, "unavailable", true),
                (429, "RENEWAL_RATE_LIMIT", .rateLimited, "too frequent", true),
                (409, "RENEWAL_IN_PROGRESS", .inProgress, "in progress", true),
                (500, "", .serverError, "server error", true),
                (401, "SESSION_EXPIRED", .sessionExpired, "expired", false),
                (401, "ACCOUNT_DISABLED", .accountDisabled, "disabled", false),
            ]
            for (code, detail, outcome, phrase, retained) in cases {
                let f = Fixture(); let (a, _) = try f.make()
                f.profile = #"{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice","orgId":"org"}"#
                f.renewable = true; f.lifetime = 100
                await f.candidate(a); await a.confirm(backup: backup)
                precondition(a.profile != nil && a.lastRenewal == nil && a.lastRenewalLine == nil)
                precondition(a.sessionExpiresAt.map { abs($0.timeIntervalSinceNow - 100) < 5 } == true)
                precondition(a.sessionValidityLine?.contains("valid until") == true)
                let before = Date()
                f.renewStatus = code; f.renewBody = "{\"detail\":\"\(detail)\"}"
                let valid = await a.ensureSession()
                precondition(f.requests.filter { $0 == "renew" }.count == 1)
                let report = a.lastRenewal!
                precondition(report.outcome == outcome && report.at >= before, "\(code) \(detail): \(report.outcome)")
                precondition(a.status.localizedCaseInsensitiveContains(phrase), "\(outcome): \(a.status)")
                precondition(!a.status.contains("xyz") && (detail.isEmpty || !a.status.contains(detail)))
                precondition(a.lastRenewalLine?.contains(outcome.rawValue) == true && a.lastRenewalLine?.contains("xyz") == false)
                if retained {
                    precondition(valid && a.profile != nil && f.credential != nil && a.sessionExpiresAt != nil)
                } else {
                    precondition(!valid && a.profile == nil && f.credential == nil && a.sessionExpiresAt == nil && a.sessionValidityLine == nil)
                }
            }
        }
        // A successful renewal records its time and code; restore reports a renewal
        // failure even when the old access token still works.
        do {
            let f = Fixture(); let (a, _) = try f.make()
            f.profile = #"{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice","orgId":"org"}"#
            f.renewable = true; f.lifetime = 100
            await f.candidate(a); await a.confirm(backup: backup)
            f.lifetime = 3600
            let renewedNow = await a.ensureSession()
            precondition(renewedNow && a.lastRenewal?.outcome == .renewed)
            precondition(a.lastRenewalLine?.contains("RENEWED") == true && a.sessionExpiresAt!.timeIntervalSinceNow > 3000)
            let fresh = FeishuAccountClient(conversations: FeishuConversationStore(), endpoint: "https://fixture.test", effects: f.effects())
            f.renewStatus = 503; f.renewBody = #"{"detail":"MEMBERSHIP_CHECK_UNAVAILABLE"}"#
            await fresh.restore()
            precondition(fresh.profile != nil && fresh.lastRenewal?.outcome == .membershipCheckUnavailable)
            precondition(fresh.status.localizedCaseInsensitiveContains("membership") && fresh.sessionValidityLine != nil)
            let gone = FeishuAccountClient(conversations: FeishuConversationStore(), endpoint: "https://fixture.test", effects: f.effects())
            f.renewStatus = 401; f.renewBody = #"{"detail":"ACCOUNT_DISABLED"}"#
            await gone.restore()
            precondition(gone.profile == nil && f.credential == nil && gone.lastRenewal?.outcome == .accountDisabled)
            precondition(gone.status.localizedCaseInsensitiveContains("disabled"))
        }
        print("PASS: production AccountClient lifecycle (offline injected effects; standalone, not XCTest)")
    }
}

#endif
