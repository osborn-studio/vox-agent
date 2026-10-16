#if FEISHU_VOICE_HARNESS
import Foundation
@main struct FeishuVoiceDraftHarness {
    @MainActor static func main() {
        var writes = 0
        var cancelledIDs: [UUID] = []
        let box = FeishuConversationOutbox(persistence: .init(load: { _ in [] }, save: { _, _ in writes += 1 }))
        let alice = FeishuConversationIdentity(origin: "https://fixture.test", appId: "cli_test", tenantKey: "team", userId: "alice")
        let bob = FeishuConversationIdentity(origin: "https://fixture.test", appId: "cli_test", tenantKey: "team", userId: "bob")
        let one = FeishuConversation(id: "fc_one", kind: "group", name: "One", capability: "text_send")
        let two = FeishuConversation(id: "fc_two", kind: "group", name: "Two", capability: "text_send")
        box.onVoiceDraftCancelled = { cancelledIDs.append($0) }
        box.bind(alice); box.select(one); box.draft = "typed"
        let first = box.beginVoiceDraft()!
        precondition(box.beginVoiceDraft() == nil)
        precondition(box.finishVoiceDraft(first, text: "spoken"))
        precondition(box.draft == "typed\nspoken" && box.records.isEmpty && writes == 0)
        precondition(!box.finishVoiceDraft(first, text: "duplicate"))
        let switched = box.beginVoiceDraft()!
        box.select(two); box.draft = "second"
        precondition(box.finishVoiceDraft(switched, text: "frozen one"))
        precondition(box.draft == "second")
        precondition(box.recoveredDrafts.last?.conversationId == one.id && box.recoveredDrafts.last?.text == "frozen one")
        let cancelled = box.beginVoiceDraft()!
        box.cancelVoiceDraft()
        precondition(!box.finishVoiceDraft(cancelled, text: "late"))
        precondition(cancelledIDs == [cancelled])
        let logout = box.beginVoiceDraft()!
        box.bind(nil); box.bind(alice); box.select(one)
        precondition(!box.finishVoiceDraft(logout, text: "previous session"))
        precondition(cancelledIDs.last == logout)
        let changed = box.beginVoiceDraft()!
        box.bind(bob); box.select(one)
        precondition(!box.finishVoiceDraft(changed, text: "alice"))
        let revoked = box.beginVoiceDraft()!
        box.revoke(one.id)
        precondition(box.finishVoiceDraft(revoked, text: "recover only"))
        precondition(box.draft.isEmpty && box.recoveredDrafts.last?.text == "recover only")
        precondition(box.records.isEmpty && writes == 0)
        box.select(one)
        let renewed = box.beginVoiceDraft()!
        box.bind(bob)
        precondition(box.finishVoiceDraft(renewed, text: "same identity survives renewal"))
        let empty = box.beginVoiceDraft()!
        let before = box.draft
        precondition(box.finishVoiceDraft(empty, text: "") && box.voiceDraftID == nil && box.draft == before)
        print("PASS: frozen destination, draft-only, duplicate/late/cancel/logout/account isolation, revoke recovery, renewal; zero durable writes")
    }
}
#endif
