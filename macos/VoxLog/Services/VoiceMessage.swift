import Foundation

struct VoiceMessage: Identifiable, Hashable {
    let id: String
    let text: String
    let rawText: String?
    let agent: String
    let createdAt: Date
    let durationSeconds: Double
    let latencyMs: Int
    let asrProvider: String?
    let isMe: Bool
    let forwardedFrom: String?

    init(id: String, text: String, rawText: String? = nil, agent: String,
         createdAt: Date, durationSeconds: Double, latencyMs: Int,
         asrProvider: String?, isMe: Bool, forwardedFrom: String? = nil) {
        self.id = id; self.text = text; self.rawText = rawText; self.agent = agent
        self.createdAt = createdAt; self.durationSeconds = durationSeconds
        self.latencyMs = latencyMs; self.asrProvider = asrProvider
        self.isMe = isMe; self.forwardedFrom = forwardedFrom
    }

    var timeString: String {
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm"
        return fmt.string(from: createdAt)
    }

    /// Can recall within 2 minutes
    var canRecall: Bool {
        Date().timeIntervalSince(createdAt) < 120
    }
}
