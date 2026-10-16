import XCTest
@testable import VoxLog

final class DeepgramASRConfigTests: XCTestCase {

    func testInit_acceptsAPIKeyAndDefaultsModel() throws {
        let config = try XCTUnwrap(DeepgramASRConfig(credentials: [
            "apiKey": "dg_test_key"
        ]))

        XCTAssertEqual(config.apiKey, "dg_test_key")
        XCTAssertEqual(config.model, DeepgramASRConfig.defaultModel)
        XCTAssertEqual(config.language, DeepgramASRConfig.defaultLanguage)
        XCTAssertTrue(config.isValid)
    }

    func testInit_rejectsMissingAPIKey() {
        XCTAssertNil(DeepgramASRConfig(credentials: [:]))
    }

    func testToCredentials_roundTripsConfiguredValues() throws {
        let config = try XCTUnwrap(DeepgramASRConfig(credentials: [
            "apiKey": "dg_test_key",
            "model": "nova-2",
        ]))

        XCTAssertEqual(config.toCredentials()["apiKey"], "dg_test_key")
        XCTAssertEqual(config.toCredentials()["model"], "nova-2")
        XCTAssertEqual(config.toCredentials()["language"], DeepgramASRConfig.defaultLanguage)
    }

    func testRegistry_exposesDeepgramProvider() {
        let entry = ASRProviderRegistry.entry(for: .deepgram)

        XCTAssertNotNil(entry)
        XCTAssertTrue(entry?.isAvailable ?? false)
        XCTAssertTrue(ASRProviderRegistry.configType(for: .deepgram) == DeepgramASRConfig.self)
        XCTAssertNotNil(ASRProviderRegistry.createClient(for: .deepgram))
    }
}
