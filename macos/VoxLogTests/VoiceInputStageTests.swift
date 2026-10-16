import XCTest
@testable import VoxLog

final class VoiceInputStageTests: XCTestCase {
    func testSilenceCountdownRequiresSpeechAndIsInterruptible() {
        let started = Date(timeIntervalSince1970: 1_000)
        var machine = VoiceInputStateMachine()
        machine.start()

        XCTAssertEqual(machine.stage, .listening)
        XCTAssertFalse(machine.sample(level: 0, at: started))
        XCTAssertEqual(machine.stage, .listening)

        XCTAssertFalse(machine.sample(level: 0.3, at: started.addingTimeInterval(0.1)))
        XCTAssertFalse(machine.sample(level: 0, at: started.addingTimeInterval(0.2)))
        XCTAssertEqual(machine.stage, .counting)
        XCTAssertEqual(machine.countdownProgress, 0, accuracy: 0.001)

        XCTAssertFalse(machine.sample(level: 0, at: started.addingTimeInterval(0.95)))
        XCTAssertEqual(machine.countdownProgress, 0.5, accuracy: 0.02)

        XCTAssertFalse(machine.sample(level: 0.3, at: started.addingTimeInterval(1.0)))
        XCTAssertEqual(machine.stage, .listening)
        XCTAssertEqual(machine.countdownProgress, 0, accuracy: 0.001)
    }

    func testCountdownCompletionTransitionsToProcessingOnce() {
        let started = Date(timeIntervalSince1970: 2_000)
        var machine = VoiceInputStateMachine()
        machine.start()
        _ = machine.sample(level: 0.4, at: started)
        _ = machine.sample(level: 0, at: started.addingTimeInterval(0.1))

        XCTAssertTrue(machine.sample(level: 0, at: started.addingTimeInterval(1.6)))
        XCTAssertEqual(machine.stage, .processing)
        XCTAssertEqual(machine.countdownProgress, 1, accuracy: 0.001)
        XCTAssertFalse(machine.sample(level: 0, at: started.addingTimeInterval(2.0)))

        machine.finalize()
        XCTAssertEqual(machine.stage, .finalized)
        machine.reset()
        XCTAssertEqual(machine.stage, .idle)
    }

    func testTranscriptDiffCountsChangedWords() {
        XCTAssertEqual(
            VoiceTranscriptDiff.changedWordCount(
                raw: "ship teh release today",
                final: "ship the release today"
            ),
            1
        )
        XCTAssertEqual(
            VoiceTranscriptDiff.changedWordCount(raw: "hello world", final: "hello brave world"),
            2
        )
        XCTAssertEqual(VoiceTranscriptDiff.changedWordCount(raw: "same", final: "same"), 0)
    }

    func testDraftsDoNotEnterGenericHistory() {
        XCTAssertFalse(RecognitionOutputDestination.composer.persistsGenericHistory)
        XCTAssertFalse(RecognitionOutputDestination.feishuDraft(UUID()).persistsGenericHistory)
        XCTAssertTrue(RecognitionOutputDestination.systemInput.persistsGenericHistory)
    }

    func testComposerOutcomeDoesNotClaimTextWasInjected() {
        let previousLanguage = UserDefaults.standard.string(forKey: "tf_language")
        UserDefaults.standard.set("en", forKey: "tf_language")
        defer { UserDefaults.standard.set(previousLanguage, forKey: "tf_language") }

        XCTAssertEqual(InjectionOutcome.retainedInComposer.completionMessage, "Ready to send")
    }
}
