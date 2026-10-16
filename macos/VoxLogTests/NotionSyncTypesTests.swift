import XCTest
@testable import VoxLog

final class NotionSyncTypesTests: XCTestCase {

    func testTextChunkerKeepsChunksBelowNotionRichTextLimit() {
        let text = String(repeating: "a", count: 4_500)
        let chunks = NotionTextChunker.chunks(text)

        XCTAssertEqual(chunks.count, 3)
        XCTAssertTrue(chunks.allSatisfy { $0.count <= NotionTextChunker.maxRichTextLength })
        XCTAssertEqual(chunks.joined(), text)
    }

    func testSettingsRoundTripDoesNotUseCredentialKeys() {
        let suiteName = "NotionSyncSettings-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let settings = NotionSyncSettings(
            isEnabled: true,
            dataSourceID: " data-source ",
            includeRawText: true,
            direction: .uploadOnly
        )
        settings.save(to: defaults)

        let loaded = NotionSyncSettings.load(from: defaults)
        XCTAssertEqual(loaded.dataSourceID, "data-source")
        XCTAssertEqual(loaded.direction, .uploadOnly)
        XCTAssertTrue(loaded.isEnabled)
        XCTAssertTrue(loaded.includeRawText)
        XCTAssertNil(defaults.string(forKey: "notion_api_token"))
    }

    func testRecordMappingUsesStableVoxLogIDAndOptionalRawText() throws {
        let record = HistoryRecord(
            id: "record-1",
            createdAt: Date(timeIntervalSince1970: 1_700_000_000),
            durationSeconds: 2,
            rawText: "raw",
            processingMode: nil,
            processedText: nil,
            finalText: "final",
            status: "completed",
            characterCount: 5,
            asrProvider: "volcano",
            agent: "general",
            deviceId: "device-a",
            syncUpdatedAt: "2026-05-29T00:00:00Z"
        )

        let properties = NotionHistoryMapper.properties(for: record, includeRawText: false)
        let data = try JSONSerialization.data(withJSONObject: properties)
        let json = String(data: data, encoding: .utf8)!

        XCTAssertTrue(json.contains("record-1"))
        XCTAssertTrue(json.contains("final"))
        XCTAssertTrue(properties.keys.contains("Name"))
        XCTAssertFalse(json.contains("raw"))
        XCTAssertTrue(json.contains("device-a"))
    }
}
