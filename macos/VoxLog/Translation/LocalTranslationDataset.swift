import Foundation
import Observation

/// Owns immutable event snapshots and the displayed user dataset. Mutation and
/// derived-state invalidation run in the same MainActor turn.
@MainActor @Observable final class LocalTranslationDataset {
    var messages: [VoiceMessage] = [] { didSet { translations?.reconcile() } }
    var agentMessages: [AgentChatMessage] = [] { didSet { translations?.reconcile() } }
    private var generation = UUID()
    weak var translations: MessageTranslationStore?
    func refresh(_ messages: [VoiceMessage]) { self.messages = messages }
    func replace(_ messages: [VoiceMessage]) {
        generation = UUID(); translations?.reconcile(); self.messages = messages
    }
    func source(local id: String) -> TranslationSource? {
        guard let message = messages.first(where: { $0.id == id }), message.isMe else { return nil }
        return TranslationSource(kind: .local, id: id, scope: generation.uuidString, text: message.text, allowed: true)
    }
    func source(agent id: UUID) -> TranslationSource? {
        guard let message = agentMessages.first(where: { $0.id == id }), message.type == .text else { return nil }
        return TranslationSource(kind: .agent, id: id.uuidString, scope: generation.uuidString, text: message.content, allowed: true)
    }
}
