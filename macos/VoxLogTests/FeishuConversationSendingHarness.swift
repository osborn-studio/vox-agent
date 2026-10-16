#if FEISHU_SENDING_HARNESS
import Foundation

@MainActor final class SendingFake: FeishuSendingTransport {
    var conversationIdentity: FeishuConversationIdentity?
    var sends: [FeishuPendingText] = []
    var receipts = 0
    var mode = "sent"
    var wait: CheckedContinuation<FeishuSendReceipt, Error>?
    func submitText(_ record: FeishuPendingText, for identity: FeishuConversationIdentity) async throws -> FeishuSendReceipt {
        sends.append(record)
        if mode == "wait" { return try await withCheckedThrowingContinuation { wait = $0 } }
        if mode == "fail" { throw FeishuSendingFailure.unavailable }
        if mode == "expired" { throw FeishuSendingFailure.expired }
        if mode == "permission" { throw FeishuSendingFailure.permission }
        return FeishuSendReceipt(eventId: record.eventId, conversationId: record.conversationId, status: "pending", reason: "QUEUED", messageId: nil)
    }
    func textReceipt(_ record: FeishuPendingText, for identity: FeishuConversationIdentity) async throws -> FeishuSendReceipt {
        receipts += 1
        return FeishuSendReceipt(eventId: record.eventId, conversationId: record.conversationId, status: mode, reason: "TEST", messageId: mode == "sent" ? "om_test" : nil)
    }
}

@main struct SendingHarness {
    @MainActor static func main() async throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1]).appendingPathComponent("pending")
        let disk = FeishuOutboxPersistence.directory(root)
        let a = FeishuConversationIdentity(origin: "https://one.test", appId: "cli_test", tenantKey: "team", userId: "alice")
        let b = FeishuConversationIdentity(origin: "https://two.test", appId: "cli_test", tenantKey: "team", userId: "alice")
        let group = FeishuConversation(id: "fc_group", kind: "group", name: "Group", capability: "text_send")
        let old = FeishuConversation(id: "fc_old", kind: "personal", name: "Old", capability: "discovery_only")
        let box = FeishuConversationOutbox(persistence: disk)
        let wire = SendingFake(); wire.conversationIdentity = a
        box.bind(a); box.select(group)
        box.draft = "hello"
        precondition(box.enqueue())
        precondition(!box.enqueue()) // synchronous freeze/clear prevents double click
        let first = box.records[0]
        precondition(first.text == "hello" && first.status == "pending")
        box.draft = "new draft"
        wire.mode = "fail"
        await box.flush(using: wire, retryNow: true)
        precondition(box.records[0].status == "unknown" && box.draft == "new draft")
        wire.mode = "sent"
        await box.flush(using: wire, retryNow: true)
        precondition(box.records[0].status == "pending" && box.records[0].accepted)
        precondition(wire.sends.map(\.eventId) == [first.eventId, first.eventId])
        await box.flush(using: wire, retryNow: true)
        precondition(box.records[0].status == "sent")
        let restored = FeishuConversationOutbox(persistence: disk)
        restored.bind(a); precondition(restored.records.count == 1)
        restored.bind(b); precondition(restored.records.isEmpty && restored.draft.isEmpty)
        restored.bind(a); precondition(restored.records[0].eventId == first.eventId)
        restored.select(old); restored.draft = "unsupported"
        precondition(!restored.enqueue())
        box.draft = "frozen"
        precondition(box.enqueue())
        wire.mode = "wait"
        let task = Task { await box.flush(using: wire, retryNow: true) }
        let waitDeadline = Date().addingTimeInterval(3)
        while wire.wait == nil && Date() < waitDeadline { await Task.yield() }
        precondition(wire.wait != nil, "bounded transport wait")
        let captured = wire.sends.last!
        box.bind(b); wire.conversationIdentity = b; box.select(group); box.draft = "other account"
        wire.wait?.resume(returning: FeishuSendReceipt(eventId: captured.eventId, conversationId: captured.conversationId, status: "sent", reason: "DELIVERED", messageId: "om_late"))
        wire.wait = nil
        await task.value
        precondition(box.records.isEmpty && box.draft == "other account")
        box.bind(a); wire.conversationIdentity = a; box.select(group)
        precondition(box.records.last!.eventId == captured.eventId)
        wire.mode = "expired"; await box.flush(using: wire, retryNow: true)
        precondition(box.identity == nil && box.records.isEmpty && box.draft.isEmpty)
        box.bind(a); box.select(group); wire.mode = "permission"
        await box.flush(using: wire, retryNow: true)
        precondition(box.identity == a && box.records.last!.status == "unknown" && box.records.last!.suspended == true)
        // Once an uncertain record loses permission, preserve uncertainty and stop
        // retrying that destination; another conversation still remains usable.
        let isolated = FeishuConversationOutbox(persistence: FeishuOutboxPersistence(load: { _ in [] }, save: { _, _ in }))
        isolated.bind(a); isolated.select(group); isolated.draft = "ambiguous"
        precondition(isolated.enqueue()); wire.mode = "fail"
        await isolated.flush(using: wire, retryNow: true)
        wire.mode = "permission"; await isolated.flush(using: wire, retryNow: true)
        precondition(isolated.records[0].status == "unknown")
        let sendCount = wire.sends.count
        await isolated.flush(using: wire)
        precondition(wire.sends.count == sendCount)
        let crowded = FeishuConversationOutbox(persistence: FeishuOutboxPersistence(load: { _ in [] }, save: { _, _ in }))
        crowded.bind(a); crowded.select(group)
        for i in 0..<21 { crowded.draft = "queued \(i)"; precondition(crowded.enqueue()) }
        let beforeBatch = wire.sends.count
        wire.mode = "unknown"
        await crowded.flush(using: wire, retryNow: true)
        await crowded.flush(using: wire, retryNow: true)
        precondition(wire.sends.count == beforeBatch + 21)
        let broken = FeishuConversationOutbox(persistence: FeishuOutboxPersistence(load: { _ in [] }, save: { _, _ in throw FeishuSendingFailure.storage }))
        broken.bind(a); broken.select(group); broken.draft = "keep me"
        precondition(!broken.enqueue() && broken.draft == "keep me" && broken.records.isEmpty)
        for url in try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil) {
            let data = try Data(contentsOf: url)
            precondition(!String(decoding: data, as: UTF8.self).contains("Bearer"))
            let mode = (try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as! NSNumber).intValue
            precondition(mode & 0o077 == 0)
        }
        print("PASS: production durable sending lifecycle (standalone assertions, not XCTest)")
    }
}

#endif
