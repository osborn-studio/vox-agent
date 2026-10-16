import Foundation

@MainActor extension FeishuTimelineModel {
    func translationSource(_ id: String) -> TranslationSource? {
        guard visible, let identity, let conversation, let revision,
              let item = items.first(where: { $0.id == id }), !item.deleted, !item.placeholder,
              let text = item.text else { return nil }
        // Old servers: personal is the bound bot DM, submission is the principal's
        // own original. Shared messages require an explicit per-item capability.
        let allowed = item.modelTranslationAllowed ?? (mode == "personal" || item.source == "submission")
        return TranslationSource(kind: .feishu, id: id,
            scope: "\(identity.origin)|\(identity.appId)|\(identity.tenantKey)|\(identity.userId)|\(conversation)|\(revision)|\(translationGeneration)",
            text: text, allowed: allowed)
    }
}

@MainActor extension FeishuConversationOutbox {
    func translationSource(_ event: String, timeline: FeishuTimelineModel) -> TranslationSource? {
        guard timeline.visible, identity == timeline.identity, let identity, let destination,
              timeline.conversation == destination.id,
              let record = visibleRecords.first(where: { $0.eventId == event }), record.status != "membership_lost",
              !timeline.suppresses(event: event, message: record.messageId, sent: record.status == "sent") else { return nil }
        return TranslationSource(kind: .original, id: event,
            scope: "\(identity.origin)|\(identity.appId)|\(identity.tenantKey)|\(identity.userId)|\(destination.id)|\(timeline.translationGeneration)",
            text: record.text, allowed: true)
    }
}

/// Production wiring shared by the app and the offline regression harness.
@MainActor enum TranslationSourceLifecycle {
    static func connect(_ translations: MessageTranslationStore, timeline: FeishuTimelineModel,
                        outbox: FeishuConversationOutbox) {
        timeline.onTranslationInvalidated = { [weak translations] in translations?.clear() }
        timeline.onTranslationSourcesChanged = { [weak translations] in translations?.reconcile() }
        outbox.onTranslationSourcesChanged = { [weak translations] in translations?.reconcile() }
    }
}
