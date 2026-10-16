import XCTest
@testable import VoxLog

final class NotionSyncEngineTests: XCTestCase {

    func testUploadCreatesOnlyMissingPagesAndPersistsCursor() async throws {
        let historyStore = HistoryStore(path: tempDBPath())
        let settings = NotionSyncSettings(
            isEnabled: true,
            dataSourceID: "ds",
            includeRawText: false,
            direction: .uploadOnly
        )
        await historyStore.insert(record(id: "r1", syncUpdatedAt: "2026-05-29T00:00:00Z"))
        await historyStore.insert(record(id: "r2", syncUpdatedAt: "2026-05-29T00:01:00Z"))
        let client = FakeNotionHistoryClient(existingIDs: ["r1"])
        let manifestURL = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).json")
        let engine = NotionSyncEngine(historyStore: historyStore, settings: settings, manifestURL: manifestURL)

        let result = await engine.sync(client: client)

        XCTAssertNil(result.error)
        XCTAssertEqual(result.uploaded, 1)
        XCTAssertEqual(result.skipped, 1)
        let createdIDs = await client.createdIDs()
        XCTAssertEqual(createdIDs, ["r2"])
    }

    func testDownloadMergesRemoteRecordsWithoutDuplicatingExistingOnes() async throws {
        let historyStore = HistoryStore(path: tempDBPath())
        let settings = NotionSyncSettings(
            isEnabled: true,
            dataSourceID: "ds",
            includeRawText: false,
            direction: .downloadOnly
        )
        await historyStore.insert(record(id: "r1", syncUpdatedAt: "2026-05-29T00:00:00Z"))
        let client = FakeNotionHistoryClient(remoteRecords: [
            record(id: "r1", syncUpdatedAt: "2026-05-29T00:00:00Z"),
            record(id: "r2", syncUpdatedAt: "2026-05-29T00:01:00Z"),
        ])
        let manifestURL = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).json")
        let engine = NotionSyncEngine(historyStore: historyStore, settings: settings, manifestURL: manifestURL)

        let result = await engine.sync(client: client)
        let all = await historyStore.fetchAll()

        XCTAssertNil(result.error)
        XCTAssertEqual(result.downloaded, 1)
        XCTAssertEqual(result.skipped, 1)
        XCTAssertEqual(Set(all.map(\.id)), ["r1", "r2"])
    }

    private func record(id: String, syncUpdatedAt: String) -> HistoryRecord {
        HistoryRecord(
            id: id,
            createdAt: ISO8601DateFormatter().date(from: syncUpdatedAt) ?? Date(),
            durationSeconds: 1,
            rawText: "raw \(id)",
            processingMode: nil,
            processedText: nil,
            finalText: "final \(id)",
            status: "completed",
            characterCount: 7,
            asrProvider: "volcano",
            agent: "general",
            deviceId: "device",
            syncUpdatedAt: syncUpdatedAt
        )
    }

    private func tempDBPath() -> String {
        FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).db").path
    }

}

private actor FakeNotionHistoryClient: NotionHistoryClient {
    private let existingIDs: Set<String>
    private var created: [String] = []
    private let remoteRecords: [HistoryRecord]

    init(existingIDs: Set<String> = [], remoteRecords: [HistoryRecord] = []) {
        self.existingIDs = existingIDs
        self.remoteRecords = remoteRecords
    }

    func pageExists(dataSourceID: String, recordID: String) async throws -> Bool {
        existingIDs.contains(recordID)
    }

    func createPage(dataSourceID: String, record: HistoryRecord, includeRawText: Bool) async throws -> String {
        created.append(record.id)
        return "page-\(record.id)"
    }

    func fetchChangedRecords(dataSourceID: String, after cursor: String?) async throws -> [HistoryRecord] {
        remoteRecords
    }

    func createdIDs() -> [String] {
        created
    }
}
