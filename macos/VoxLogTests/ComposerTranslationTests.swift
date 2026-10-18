import XCTest
@testable import VoxLog

@MainActor
final class ComposerTranslationTests: XCTestCase {
    private func configuration(_ provider: LLMProvider = .openai) throws -> TranslationConfiguration {
        try TranslationConfiguration(provider: provider, baseURL: "https://api.example.invalid/v1", model: "gpt-4o",
                                     credential: "k", generation: 1, bypassProxy: false)
    }

    func testDirectionFollowsTheDraftScript() {
        XCTAssertEqual(ComposerTranslationDirection.target(for: "Hello, how are you today?"), .simplifiedChinese)
        XCTAssertEqual(ComposerTranslationDirection.target(for: "我们今天讨论 composer 的设计"), .english)
        XCTAssertEqual(ComposerTranslationDirection.target(for: "Ship the composer redesign by 周五"), .simplifiedChinese)
        XCTAssertEqual(ComposerTranslationDirection.target(for: "こんにちは、東京駅はどこですか"), .simplifiedChinese)
        XCTAssertEqual(ComposerTranslationDirection.target(for: "안녕하세요"), .simplifiedChinese)
        XCTAssertEqual(ComposerTranslationDirection.target(for: "12345 !!!"), .simplifiedChinese)
    }

    func testProtocolCarriesTheTargetInstructionAndKeepsTheDefault() throws {
        let config = try configuration()
        let english = try TranslationProtocol.request("hi", config: config, target: .english)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(english.httpBody)) as? [String: Any])
        let messages = try XCTUnwrap(body["messages"] as? [[String: Any]])
        XCTAssertEqual(messages[0]["content"] as? String, TranslationTarget.english.instruction)
        XCTAssertTrue(TranslationTarget.english.instruction.contains("英文"))

        let chinese = try TranslationProtocol.request("hi", config: config)
        let defaultBody = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(chinese.httpBody)) as? [String: Any])
        let defaultMessages = try XCTUnwrap(defaultBody["messages"] as? [[String: Any]])
        XCTAssertEqual(defaultMessages[0]["content"] as? String, TranslationProtocol.instruction)
        XCTAssertEqual(TranslationProtocol.instruction, TranslationTarget.simplifiedChinese.instruction)
    }

    func testTranslatorUsesTheSharedConfigurationAndDetectedTarget() async throws {
        let config = try configuration()
        var seen: (String, TranslationTarget)?
        let result = try await ComposerTranslator.translate(
            "  Hello world  ",
            snapshot: { config },
            completion: { source, _, target in
                seen = (source, target)
                return TranslationCompletion(text: "你好，世界\n", finishReason: "stop", complete: true)
            }
        )
        XCTAssertEqual(seen?.0, "Hello world")
        XCTAssertEqual(seen?.1, .simplifiedChinese)
        XCTAssertEqual(result, ComposerTranslationResult(text: "你好，世界", target: .simplifiedChinese, unchanged: false))
    }

    func testCodeOnlyDraftIsNeverSentToTheModel() async throws {
        var called = false
        let result = try await ComposerTranslator.translate(
            "```\nlet x = 1\n```",
            snapshot: { XCTFail("no configuration read"); throw TranslationFailure.configuration },
            completion: { _, _, _ in called = true; throw TranslationFailure.network }
        )
        XCTAssertFalse(called)
        XCTAssertTrue(result.unchanged)
    }

    func testTranslatorRejectsEmptyOversizedIncompleteAndUnprotectedOutput() async throws {
        let config = try configuration()
        func expect(_ failure: TranslationFailure, _ draft: String, finish: String = "stop", output: String = "译文") async {
            do {
                _ = try await ComposerTranslator.translate(draft, snapshot: { config }, completion: { _, _, _ in
                    TranslationCompletion(text: output, finishReason: finish, complete: true)
                })
                XCTFail("expected \(failure)")
            } catch {
                XCTAssertEqual(error as? TranslationFailure, failure)
            }
        }
        await expect(.empty, "   ")
        await expect(.inputLimit, String(repeating: "a", count: ComposerTranslator.maxBytes + 1))
        await expect(.incomplete, "hello", finish: "length")
        await expect(.protection, "see https://example.invalid/doc now", output: "现在看文档")
        await expect(.empty, "hello", output: " \n")
    }

    func testTranslatorHonoursTheSharedAdmissionLimit() async throws {
        let config = try configuration()
        let first = try XCTUnwrap(TranslationAdmission.shared.acquire())
        let second = try XCTUnwrap(TranslationAdmission.shared.acquire())
        defer { TranslationAdmission.shared.release(first); TranslationAdmission.shared.release(second) }
        do {
            _ = try await ComposerTranslator.translate("hello", snapshot: { config }, completion: { _, _, _ in
                XCTFail("must not run while two translations are admitted")
                return TranslationCompletion(text: "x", finishReason: "stop", complete: true)
            })
            XCTFail("expected busy")
        } catch {
            XCTAssertEqual(error as? TranslationFailure, .busy)
        }
    }
}
