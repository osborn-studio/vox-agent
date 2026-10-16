import Foundation

struct VoiceInputStateMachine: Equatable {
    enum Stage: String, Equatable, Sendable {
        case idle
        case listening
        case counting
        case processing
        case finalized
    }

    static let pauseDuration: TimeInterval = 1.5
    static let speechThreshold: Float = 0.035

    private(set) var stage: Stage = .idle
    private(set) var countdownProgress: Double = 0
    private var hasDetectedSpeech = false
    private var silenceStartedAt: Date?

    mutating func start() {
        stage = .listening
        countdownProgress = 0
        hasDetectedSpeech = false
        silenceStartedAt = nil
    }

    /// Samples real microphone amplitude. Returns true exactly once when the
    /// interruptible pause countdown finishes and recording should stop.
    mutating func sample(level: Float, at now: Date) -> Bool {
        guard stage == .listening || stage == .counting else { return false }

        if level >= Self.speechThreshold {
            hasDetectedSpeech = true
            silenceStartedAt = nil
            countdownProgress = 0
            stage = .listening
            return false
        }

        guard hasDetectedSpeech else { return false }
        if silenceStartedAt == nil {
            silenceStartedAt = now
            countdownProgress = 0
            stage = .counting
            return false
        }

        let elapsed = max(0, now.timeIntervalSince(silenceStartedAt!))
        countdownProgress = min(1, elapsed / Self.pauseDuration)
        guard countdownProgress >= 1 else { return false }

        stage = .processing
        silenceStartedAt = nil
        return true
    }

    mutating func beginProcessing() {
        guard stage == .listening || stage == .counting else { return }
        stage = .processing
        silenceStartedAt = nil
    }

    mutating func finalize() {
        stage = .finalized
        countdownProgress = 0
        silenceStartedAt = nil
    }

    mutating func reset() {
        stage = .idle
        countdownProgress = 0
        hasDetectedSpeech = false
        silenceStartedAt = nil
    }
}

enum VoiceTranscriptDiff {
    static func changedWordCount(raw: String, final: String) -> Int {
        let rawWords = words(in: raw)
        let finalWords = words(in: final)
        let sharedCount = min(rawWords.count, finalWords.count)
        let replacements = zip(rawWords.prefix(sharedCount), finalWords.prefix(sharedCount))
            .count { $0 != $1 }
        return replacements + abs(rawWords.count - finalWords.count)
    }

    private static func words(in value: String) -> [String] {
        value
            .split(whereSeparator: { $0.isWhitespace })
            .map { $0.lowercased() }
    }
}
