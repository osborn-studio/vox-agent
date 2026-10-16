#if FEISHU_SENDING_ACCOUNT_HARNESS
import Foundation

@MainActor final class SendingAccountEffects {
    var wireStatus = 202
    var profileUser = "alice"
    var overrideBody: Data?
    var held: CheckedContinuation<(Data, Int), Error>?
    var hold = false
    var deleteHeld: CheckedContinuation<Bool, Never>?
    var holdDelete = false
    var deletes = 0
    var sent: [URLRequest] = []
    var replyStatus = "pending"
    var record: FeishuPendingText?
    var credential: String? = try! InputBackupClient.bindCredential("vox_sso_synthetic", to: URL(string: "https://fixture.test")!)
    func effects() -> FeishuAccountEffects {
        FeishuAccountEffects(send: { req in
            if req.url!.lastPathComponent == "me" {
                return (Data(#"{"profile":{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice"},"targetIds":[]}"#.replacingOccurrences(of: "alice", with: self.profileUser).utf8), 200)
            }
            if req.url!.lastPathComponent == "conversations" {
                return (Data(#"{"identity":{"appId":"cli_test","tenantKey":"team","userId":"alice"},"conversations":[{"id":"fc_dm","kind":"personal","name":"Mirror","capability":"text_send"}]}"#.utf8), 200)
            }
            self.sent.append(req)
            if self.hold { return try await withCheckedThrowingContinuation { self.held = $0 } }
            if let body = self.overrideBody { return (body, self.wireStatus) }
            let receipt = FeishuSendReceipt(eventId: self.record!.eventId, conversationId: self.record!.conversationId,
                status: self.replyStatus, reason: "STATIC", messageId: self.replyStatus == "sent" ? "om_one" : nil)
            return (try JSONEncoder().encode(receipt), self.wireStatus)
        }, load: { self.credential }, save: { self.credential = $0 }, delete: {
            self.deletes += 1
            if self.holdDelete {
                let deleted = await withCheckedContinuation { self.deleteHeld = $0 }
                if deleted { self.credential = nil }
                return deleted
            }
            self.credential = nil; return true
        }, pauseBackup: {}, resetLogin: {}, completeLogin: { _ in }, persistEndpoint: { _ in }, browser: { _, _ in fatalError("browser forbidden") })
    }
}

@main struct SendingAccountHarness {
    @MainActor static func main() async throws {
        let path = URL(fileURLWithPath: CommandLine.arguments[1]).appendingPathComponent("account-pending")
        let outbox = FeishuConversationOutbox(persistence: .directory(path))
        let nav = FeishuConversationStore()
        var navigationChanges = 0
        // Same callback routing as production AppState (including fallback selection).
        nav.onNavigationChange = { [weak nav, weak outbox] in
            navigationChanges += 1
            guard let nav else { return }
            outbox?.select(nav.conversations.first { nav.selection == .feishu($0.id) })
        }
        outbox.onForbidden = { [weak nav] id in nav?.conversationForbidden(id) }
        let f = SendingAccountEffects()
        let account = FeishuAccountClient(conversations: nav, outbox: outbox, endpoint: "https://fixture.test", effects: f.effects())
        await account.restore()
        let identity = account.conversationIdentity!
        nav.refresh(using: account)
        let navigationDeadline = Date().addingTimeInterval(3)
        while nav.loading && Date() < navigationDeadline { await Task.yield() }
        precondition(!nav.loading && nav.conversations.count == 1)
        nav.select(.feishu("fc_dm"))
        let response = try await account.discoverConversations(for: identity)
        precondition(response.conversations[0].capability == "text_send")
        outbox.select(response.conversations[0]); outbox.draft = "private synthetic text"
        precondition(outbox.enqueue()); f.record = outbox.records[0]
        await outbox.flush(using: account, retryNow: true)
        precondition(outbox.records[0].accepted && outbox.records[0].status == "pending")
        let request = f.sent[0]
        let body = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: String]
        precondition(Set(body.keys) == Set(["eventId", "text"]))
        precondition(request.value(forHTTPHeaderField: "Authorization") == "Bearer vox_sso_synthetic")
        precondition(request.url!.path == "/v1/conversations/fc_dm/messages")
        // Delayed accepted receipt 403 cannot establish non-delivery or erase a newer draft.
        f.hold = true
        let revoked = Task { await outbox.flush(using: account, retryNow: true) }
        let revokedDeadline = Date().addingTimeInterval(3)
        while f.held == nil && Date() < revokedDeadline { await Task.yield() }
        precondition(f.held != nil && f.sent.last!.httpMethod == "GET")
        outbox.draft = "newer recoverable synthetic draft"
        let priorNavigationChanges = navigationChanges
        f.held!.resume(returning: (Data("{}".utf8), 403)); f.held = nil
        await revoked.value; f.hold = false
        precondition(outbox.records[0].status == "unknown" && outbox.records[0].accepted)
        precondition(nav.selection.agentID != nil && outbox.destination == nil)
        precondition(outbox.attentionRecords.count == 1 && outbox.hasRetryableOriginals)
        precondition(navigationChanges == priorNavigationChanges + 1)
        precondition(outbox.recoveredDrafts.first?.text == "newer recoverable synthetic draft")
        precondition(outbox.recoveredDrafts.first?.conversationId == "fc_dm")
        f.wireStatus = 200; f.replyStatus = "sent"
        await outbox.flush(using: account, retryNow: true)
        precondition(f.sent.last!.httpMethod == "GET" && f.sent.last!.httpBody == nil)
        precondition(outbox.records[0].status == "sent")
        // A held old-account response cannot clear a new draft or drain another origin.
        outbox.select(response.conversations[0]); outbox.draft = "captured"; precondition(outbox.enqueue()); f.record = outbox.records.last!
        f.hold = true
        let flush = Task { await outbox.flush(using: account, retryNow: true) }
        let heldDeadline = Date().addingTimeInterval(3)
        while f.held == nil && Date() < heldDeadline { await Task.yield() }
        precondition(f.held != nil, "bounded response wait")
        account.endpoint = "https://other.test"
        precondition(outbox.identity == nil && outbox.records.isEmpty && outbox.draft.isEmpty)
        f.held!.resume(returning: (Data("{}".utf8), 401)); f.held = nil
        await flush.value
        precondition(f.deletes == 0 && account.conversationIdentity == nil)
        f.hold = false; account.endpoint = "https://fixture.test"
        await account.restore()
        precondition(outbox.records.count == 2)
        // Prior unknown plus a bare conflict cannot establish non-delivery.
        f.wireStatus = 409
        await outbox.flush(using: account, retryNow: true)
        precondition(outbox.records.last!.status == "unknown" && outbox.records.last!.suspended == true)
        // Conversation 403 does not invalidate the account.
        f.wireStatus = 403
        await outbox.flush(using: account, retryNow: true)
        precondition(account.conversationIdentity == identity && outbox.records.last!.status == "unknown" && outbox.records.last!.suspended == true)
        outbox.select(response.conversations[0]); outbox.draft = "expire"; precondition(outbox.enqueue())
        f.record = outbox.records.last!; f.wireStatus = 401; f.holdDelete = true
        let expiry = Task { await outbox.flush(using: account, retryNow: true) }
        let deleteDeadline = Date().addingTimeInterval(3)
        while f.deleteHeld == nil && Date() < deleteDeadline { await Task.yield() }
        precondition(f.deleteHeld != nil, "bounded expiry wait")
        precondition(account.conversationIdentity == nil && outbox.records.isEmpty && outbox.draft.isEmpty)
        f.deleteHeld!.resume(returning: true); f.deleteHeld = nil
        await expiry.value
        precondition(f.deletes == 1 && f.credential == nil)
        precondition(nav.conversations.isEmpty && outbox.recoveredDrafts.isEmpty)
        print("PASS: real AccountClient accepted receipt403 keeps unknown; AppState callback recovers newer draft after fallback; revoke once; 401 clears credentials/store/outbox")
        // A mixed-type invalid-principal body still performs account-level invalidation.
        f.holdDelete = false
        f.credential = try InputBackupClient.bindCredential("vox_sso_synthetic", to: URL(string: "https://fixture.test")!)
        await account.restore()
        precondition(outbox.records.count == 3)
        f.overrideBody = Data(#"{"detail":"FEISHU_PRINCIPAL_REQUIRED","extra":42}"#.utf8)
        f.wireStatus = 403
        await outbox.flush(using: account, retryNow: true)
        precondition(account.conversationIdentity == nil && f.credential == nil && outbox.records.isEmpty && f.deletes == 2)
        f.overrideBody = nil
        f.profileUser = "bob"
        f.credential = try InputBackupClient.bindCredential("vox_sso_synthetic", to: URL(string: "https://fixture.test")!)
        await account.restore()
        precondition(account.conversationIdentity?.userId == "bob" && outbox.records.isEmpty)
        account.endpoint = "https://other.test"
        f.profileUser = "alice"; account.endpoint = "https://fixture.test"
        await account.restore()
        precondition(account.conversationIdentity == identity && outbox.records.count == 3)
        precondition(outbox.records[0].eventId == body["eventId"])
        print("PASS: production identity rotation isolates accounts; same identity reloads original events; mixed-type principal403 expires account")
        // Final audit regressions use the real AccountClient, never a fake receipt adapter.
        for previouslyAccepted in [false, true] {
            let fx = SendingAccountEffects()
            var saved: [FeishuPendingText] = []
            var clock = Date()
            let q = FeishuConversationOutbox(persistence: .init(load: { _ in saved }, save: { _, rows in saved = rows }), now: { clock })
            let client = FeishuAccountClient(conversations: FeishuConversationStore(), outbox: q, endpoint: "https://fixture.test", effects: fx.effects())
            await client.restore()
            q.select(FeishuConversation(id: "fc_dm", kind: "personal", name: "Mirror", capability: "text_send"))
            q.draft = "synthetic mismatch"; precondition(q.enqueue()); fx.record = q.records[0]
            if previouslyAccepted { await q.flush(using: client, retryNow: true) }
            fx.wireStatus = previouslyAccepted ? 200 : 202
            fx.overrideBody = try JSONEncoder().encode(FeishuSendReceipt(eventId: fx.record!.eventId, conversationId: "fc_other", status: "pending", reason: "QUEUED", messageId: nil))
            await q.flush(using: client, retryNow: true)
            precondition(q.records[0].status == "unknown" && q.records[0].suspended == true, "mismatched real-client receipt must quarantine")
            let count = fx.sent.count; clock = clock.addingTimeInterval(1000)
            await q.flush(using: client); precondition(fx.sent.count == count)
            let reopened = FeishuConversationOutbox(persistence: .init(load: { _ in saved }, save: { _, rows in saved = rows }))
            reopened.bind(client.conversationIdentity)
            precondition(reopened.hasRetryableOriginals, "stopped original remains actionable after relaunch")
        }
        print("PASS: real-client wrong-conversation receipt quarantines immediately; stopped original remains retryable after reopen")
        do {
            let fx = SendingAccountEffects(); var saved: [FeishuPendingText] = []
            let q = FeishuConversationOutbox(persistence: .init(load: { _ in saved }, save: { _, rows in saved = rows }))
            let client = FeishuAccountClient(conversations: FeishuConversationStore(), outbox: q, endpoint: "https://fixture.test", effects: fx.effects())
            await client.restore(); q.select(FeishuConversation(id: "fc_dm", kind: "personal", name: "Mirror", capability: "text_send"))
            q.draft = "synthetic polling"; precondition(q.enqueue()); fx.record = q.records[0]
            fx.wireStatus = 503
            for _ in 0..<5 { await q.flush(using: client, retryNow: true) }
            fx.wireStatus = 202
            await q.flush(using: client, retryNow: true)
            precondition(q.records[0].accepted && q.records[0].status == "pending" && q.records[0].suspended != true, "successful sixth request must begin receipt polling")
            fx.wireStatus = 200; fx.replyStatus = "sending"
            for _ in 0..<8 { await q.flush(using: client, retryNow: true) }
            precondition(q.records[0].status == "sending" && q.records[0].suspended != true && q.records[0].attempts == 0, "healthy polls do not exhaust error budget")
        }
        print("PASS: successful acceptance resets failed POST budget; healthy receipt polling preserves authoritative state")
        do {
            var finalRows = (0..<200).map { i in
                FeishuPendingText(eventId: "final-\(i)", conversationId: "fc_dm", text: "synthetic", status: "unknown", accepted: true, messageId: nil, suspended: true, attempted: true, final: true)
            }
            let q = FeishuConversationOutbox(persistence: .init(load: { _ in finalRows }, save: { _, rows in finalRows = rows }))
            q.bind(identity); q.select(FeishuConversation(id: "fc_dm", kind: "personal", name: "Mirror", capability: "text_send")); q.draft = "next synthetic"
            precondition(q.canSend && q.enqueue(), "server-final cache cannot permanently block new submissions")
            precondition(q.records.count == 51 && q.records.dropLast().allSatisfy { $0.status == "unknown" && $0.final == true }, "compact only server-final cache, preserve uncertainty")
        }
        print("PASS: server-final unknown cache compacts without claiming delivery; service retains originals")
        print("PASS: production account send/receipt, capability, expiry and stale-response isolation")
    }
}
#endif
