#if FEISHU_READING_HARNESS
import Foundation

@MainActor final class ReadingEffects {
    var status = 200
    var body = Data()
    var hold = false
    var held: CheckedContinuation<(Data, Int), Error>?
    var deleteHeld: CheckedContinuation<Bool, Never>?
    var holdDelete = false
    var calls: [URLRequest] = []
    func effects() -> FeishuAccountEffects {
        FeishuAccountEffects(send: { req in
            if req.url!.lastPathComponent == "me" {
                return (Data(#"{"profile":{"name":"Alice","tenantKey":"team","openId":"ou_alice","appId":"cli_test","userId":"alice"},"targetIds":[]}"#.utf8), 200)
            }
            self.calls.append(req)
            if self.hold { return try await withCheckedThrowingContinuation { self.held = $0 } }
            return (self.body, self.status)
        }, load: { try! InputBackupClient.bindCredential("vox_sso_synthetic", to: URL(string: "https://fixture.test")!) },
        save: { _ in }, delete: {
            if self.holdDelete { return await withCheckedContinuation { self.deleteHeld = $0 } }
            return true
        }, pauseBackup: {}, resetLogin: {}, completeLogin: { _ in }, persistEndpoint: { _ in },
        browser: { _, _ in fatalError("real browser forbidden") })
    }
    func batch(_ ids: [Int], cursor: String?) {
        page(cursor: cursor)
        var object = try! JSONSerialization.jsonObject(with: body) as! [String: Any]
        let template = (object["items"] as! [[String: Any]])[0]
        object["items"] = ids.map { id in
            var row = template
            row["messageId"] = "om_\(id)"; row["eventId"] = NSNull()
            row["createdAt"] = String(id); row["updatedAt"] = String(id)
            return row
        }
        body = try! JSONSerialization.data(withJSONObject: object)
    }
    func page(text: String? = "current", deleted: Bool = false, updated: String = "1000", mode: String = "personal", revision: String = String(repeating: "a", count: 64), cursor: String? = String(repeating: "x", count: 43)) {
        let item: [String: Any] = ["messageId": "om_one", "eventId": "event1", "source": "feishu", "text": text as Any? ?? NSNull(),
            "createdAt": "1000", "updatedAt": updated, "deleted": deleted, "updated": updated != "1000", "placeholder": false,
            "authorKind": "vox", "authorId": "alice", "authorName": "Alice", "status": NSNull()]
        body = try! JSONSerialization.data(withJSONObject: ["conversationId": "fc_dm", "mode": mode, "policyRevision": revision,
            "items": mode == "own_submissions" ? [] : [item], "nextCursor": cursor as Any? ?? NSNull(), "hasMore": cursor != nil])
    }
}

@main struct ReadingHarness {
    @MainActor static func wait(_ condition: () -> Bool) async {
        let end = Date().addingTimeInterval(3)
        while !condition() && Date() < end { await Task.yield() }
        precondition(condition(), "bounded wait")
    }
    @MainActor static func main() async throws {
        let f = ReadingEffects(); f.page()
        let nav = FeishuConversationStore()
        let account = FeishuAccountClient(conversations: nav, endpoint: "https://fixture.test", effects: f.effects())
        let model = account.timeline
        await account.restore()
        model.select("fc_dm")
        model.setVisible(true)
        await model.refresh(using: account)
        precondition(model.items.first?.text == "current")
        precondition(f.calls.last!.url!.path == "/v1/conversations/fc_dm/messages")
        precondition(f.calls.last!.value(forHTTPHeaderField: "Cache-Control") == "no-store")
        let firstCursor = model.nextCursor
        f.page(text: "edited", updated: "1001", cursor: String(repeating: "y", count: 43))
        await model.refresh(using: account)
        precondition(model.items.count == 1 && model.items[0].text == "edited" && model.nextCursor == firstCursor)
        f.page(text: nil, deleted: true, updated: "1000")
        await model.refresh(using: account)
        precondition(model.items[0].deleted && model.items[0].text == nil)
        f.page() // Overlap from an older snapshot cannot resurrect a withdrawal.
        await model.refresh(using: account, older: true)
        precondition(model.items[0].deleted)
        precondition(model.suppresses(event: "event1", message: "om_one", sent: true))
        f.page(mode: "own_submissions", revision: String(repeating: "b", count: 64), cursor: nil)
        await model.refresh(using: account)
        precondition(model.items.isEmpty && model.mode == "own_submissions" && model.nextCursor == nil)
        f.page(); f.hold = true
        let old = Task { await model.refresh(using: account) }
        await wait { f.held != nil }
        model.setVisible(false)
        precondition(model.items.isEmpty && model.nextCursor == nil)
        f.held!.resume(returning: (f.body, 200)); f.held = nil
        await old.value
        precondition(model.items.isEmpty)
        f.hold = false; model.setVisible(true)
        await model.refresh(using: account)
        precondition(!model.items.isEmpty)
        f.hold = true
        let selected = Task { await model.refresh(using: account) }
        await wait { f.held != nil }
        model.select("fc_other")
        f.held!.resume(returning: (f.body, 200)); f.held = nil
        await selected.value
        precondition(model.items.isEmpty && model.conversation == "fc_other")
        model.select("fc_dm"); f.hold = false
        await model.refresh(using: account)
        account.endpoint = "https://other.test"
        precondition(model.items.isEmpty && model.identity == nil)
        account.endpoint = "https://fixture.test"; await account.restore()
        model.select("fc_dm"); model.setVisible(true)
        await model.refresh(using: account)
        f.holdDelete = true; f.status = 401
        let expired = Task { await model.refresh(using: account) }
        await wait { f.deleteHeld != nil }
        precondition(model.items.isEmpty && model.identity == nil, "clear before credential await")
        f.deleteHeld!.resume(returning: true); f.deleteHeld = nil
        await expired.value
        f.status = 200; f.holdDelete = false; await account.restore()
        model.select("fc_dm"); model.setVisible(true)
        await model.refresh(using: account)
        f.status = 403
        await model.refresh(using: account)
        precondition(model.items.isEmpty && model.conversation == nil && account.conversationIdentity != nil)
        // Same-identity relogin rejects the prior session response even after new content loads.
        f.status = 200; model.select("fc_dm"); f.hold = true
        let staleSession = Task { await model.refresh(using: account) }
        await wait { f.held != nil }
        account.endpoint = "https://other.test"; account.endpoint = "https://fixture.test"
        await account.restore(); model.select("fc_dm"); f.hold = false
        f.page(text: "new session", updated: "1003")
        await model.refresh(using: account)
        f.held!.resume(returning: (Data("{}".utf8), 401)); f.held = nil
        await staleSession.value
        precondition(model.items.first?.text == "new session" && account.conversationIdentity != nil)
        // Real production parsing rejects malformed flags, oversized pages and leaked placeholders.
        let valid = f.body
        for mutation in 0..<5 {
            var object = try JSONSerialization.jsonObject(with: valid) as! [String: Any]
            var rows = object["items"] as! [[String: Any]]
            switch mutation {
            case 0: rows[0]["deleted"] = "false"
            case 1: rows[0]["createdAt"] = "1.5"
            case 2: rows[0]["placeholder"] = true
            case 3: object["conversationId"] = "fc_wrong"
            default: rows = Array(repeating: rows[0], count: 51)
            }
            object["items"] = rows
            f.body = try JSONSerialization.data(withJSONObject: object)
            await model.refresh(using: account)
            precondition(model.items.isEmpty && model.failure == .invalid)
        }
        // A previously complete short history can grow past the latest page.
        f.page(cursor: nil)
        await model.refresh(using: account)
        precondition(model.nextCursor == nil)
        f.page(cursor: String(repeating: "z", count: 43))
        await model.refresh(using: account)
        precondition(model.nextCursor != nil, "new insertion must expose older paging after an exhausted chain")
        f.body = valid
        await model.refresh(using: account)
        // Selection-owned errors cannot revoke a different current selection.
        f.hold = true
        let staleForbidden = Task { await model.refresh(using: account) }
        await wait { f.held != nil }
        model.select("fc_other")
        f.held!.resume(returning: (Data("{}".utf8), 403)); f.held = nil
        await staleForbidden.value
        precondition(model.conversation == "fc_other" && account.conversationIdentity != nil)
        f.hold = false
        // A sliding latest window must not erase the bridge to previously loaded pages.
        model.select("fc_other"); model.select("fc_dm")
        f.batch(Array(51...100), cursor: String(repeating: "a", count: 43))
        await model.refresh(using: account)
        f.batch(Array(1...50), cursor: String(repeating: "b", count: 43))
        await model.refresh(using: account, older: true)
        let olderCursor = model.nextCursor
        f.batch(Array(52...101), cursor: String(repeating: "c", count: 43))
        await model.refresh(using: account)
        precondition(model.items.count == 101 && model.items.contains { $0.messageId == "om_51" }, "latest window must keep displaced rows")
        precondition(model.nextCursor == olderCursor)
        f.batch(Array(200...249), cursor: String(repeating: "d", count: 43))
        await model.refresh(using: account)
        precondition(model.items.count == 50 && model.nextCursor != olderCursor, "a disjoint head must restart pagination instead of hiding a gap")
        precondition(model.historyRestarted)
        f.batch(Array(150...199), cursor: nil)
        await model.refresh(using: account, older: true)
        precondition(!model.historyRestarted)
        f.page()
        // Production visibility coordinator with injected timer; no NSApplication launch.
        var tick: (@MainActor () -> Void)?
        var cancellations = 0
        let coordinator = FeishuTimelineLifecycle(model: model, account: account, schedule: { action in
            tick = action; return { cancellations += 1 }
        })
        f.status = 200; model.select("fc_dm")
        coordinator.visibilityChanged(true)
        await wait { !model.loading }
        coordinator.visibilityChanged(false)
        let count = f.calls.count
        tick?(); await Task.yield()
        precondition(cancellations == 1 && model.items.isEmpty && f.calls.count == count)
        print("PASS: production reading transport, merge, authorization, async ownership and visibility")
    }
}
#endif
