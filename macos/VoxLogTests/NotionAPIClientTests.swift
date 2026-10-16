import XCTest
@testable import VoxLog

final class NotionAPIClientTests: XCTestCase {

    func testCreatePageBodyUsesDataSourceParentAndChildren() throws {
        let record = sampleRecord(finalText: String(repeating: "x", count: 2_100))

        let body = NotionAPIClient.createPageBody(
            dataSourceID: "ds1",
            record: record,
            includeRawText: true
        )
        let data = try JSONSerialization.data(withJSONObject: body)
        let json = String(data: data, encoding: .utf8)!

        XCTAssertTrue(json.contains("data_source_id"))
        XCTAssertTrue(json.contains("ds1"))
        XCTAssertTrue(json.contains("Raw Text"))
        XCTAssertTrue(json.contains("VoxLog ID"))
    }

    func testRecordParsesFromNotionPageProperties() {
        let page: [String: Any] = [
            "properties": [
                "Name": ["title": [["plain_text": "hello"]]],
                "VoxLog ID": rich("r1"),
                "Created At": date("2026-05-29T00:00:00Z"),
                "Final Text": rich("hello"),
                "Raw Text": rich("raw hello"),
                "Agent": rich("general"),
                "ASR Provider": rich("volcano"),
                "Device ID": rich("macbook"),
                "Character Count": ["number": 5],
                "Sync Updated At": date("2026-05-29T01:00:00Z"),
                "Status": ["select": ["name": "completed"]],
            ],
        ]

        let record = NotionAPIClient.record(from: page)

        XCTAssertEqual(record?.id, "r1")
        XCTAssertEqual(record?.finalText, "hello")
        XCTAssertEqual(record?.rawText, "raw hello")
        XCTAssertEqual(record?.agent, "general")
        XCTAssertEqual(record?.deviceId, "macbook")
    }

    func testClientRetriesRateLimitedRequest() async throws {
        let transport = FakeNotionTransport(responses: [
            (429, ["Retry-After": "0"], "{}"),
            (200, [:], #"{"results":[]}"#),
        ])
        let client = NotionAPIClient(
            token: "token",
            transport: transport,
            baseURL: URL(string: "https://notion.test/v1")!,
            sleeper: { _ in }
        )

        let exists = try await client.pageExists(dataSourceID: "ds", recordID: "r1")

        XCTAssertFalse(exists)
        let requestCount = await transport.currentRequestCount()
        XCTAssertEqual(requestCount, 2)
        let lastURL = await transport.currentLastURL()
        XCTAssertEqual(lastURL?.path, "/v1/data_sources/ds/query")
    }

    private func sampleRecord(finalText: String = "final") -> HistoryRecord {
        HistoryRecord(
            id: "r1",
            createdAt: Date(timeIntervalSince1970: 1_700_000_000),
            durationSeconds: 1,
            rawText: "raw",
            processingMode: nil,
            processedText: nil,
            finalText: finalText,
            status: "completed",
            characterCount: finalText.count,
            asrProvider: "volcano",
            agent: "general"
        )
    }

    private func rich(_ value: String) -> [String: Any] {
        ["rich_text": [["plain_text": value]]]
    }

    private func date(_ value: String) -> [String: Any] {
        ["date": ["start": value]]
    }
}

private actor FakeNotionTransport: NotionTransport {
    private var responses: [(Int, [String: String], String)]
    private(set) var requestCount = 0
    private var lastURL: URL?

    init(responses: [(Int, [String: String], String)]) {
        self.responses = responses
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requestCount += 1
        lastURL = request.url
        let response = responses.removeFirst()
        let http = HTTPURLResponse(
            url: request.url!,
            statusCode: response.0,
            httpVersion: nil,
            headerFields: response.1
        )!
        return (Data(response.2.utf8), http)
    }

    func currentRequestCount() -> Int {
        requestCount
    }

    func currentLastURL() -> URL? {
        lastURL
    }
}
