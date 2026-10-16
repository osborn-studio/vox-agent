import XCTest
@testable import VoxLog

final class AssemblyAIASRConfigTests: XCTestCase {

    func testInit_usesDefaultModelWhenMissing() throws {
        let config = try XCTUnwrap(AssemblyAIASRConfig(credentials: [
            "apiKey": "aa_test_key",
        ]))

        XCTAssertEqual(config.model, AssemblyAIASRConfig.defaultModel)
        XCTAssertTrue(config.isValid)
    }

    func testInit_rejectsMissingAPIKey() {
        XCTAssertNil(AssemblyAIASRConfig(credentials: [:]))
        XCTAssertNil(AssemblyAIASRConfig(credentials: ["apiKey": "   "]))
    }

    func testInit_fallsBackToDefaultModelForUnsupportedValue() throws {
        let config = try XCTUnwrap(AssemblyAIASRConfig(credentials: [
            "apiKey": "aa_test_key",
            "model": "whisper-rt",
        ]))

        XCTAssertEqual(config.model, AssemblyAIASRConfig.defaultModel)
    }

    func testToCredentials_roundTrips() throws {
        let config = try XCTUnwrap(AssemblyAIASRConfig(credentials: [
            "apiKey": "aa_test_key",
            "model": "u3-rt-pro",
        ]))

        XCTAssertEqual(
            config.toCredentials(),
            [
                "apiKey": "aa_test_key",
                "model": "u3-rt-pro",
            ]
        )
    }
}
