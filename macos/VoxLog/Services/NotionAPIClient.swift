import Foundation

protocol NotionTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

protocol NotionHistoryClient: Sendable {
    func pageExists(dataSourceID: String, recordID: String) async throws -> Bool
    func createPage(dataSourceID: String, record: HistoryRecord, includeRawText: Bool) async throws -> String
    func fetchChangedRecords(dataSourceID: String, after cursor: String?) async throws -> [HistoryRecord]
}

struct URLSessionNotionTransport: NotionTransport {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw NotionAPIError.invalidResponse("Missing HTTP response")
        }
        return (data, http)
    }
}

enum NotionAPIError: Error, LocalizedError, Equatable {
    case missingToken
    case invalidResponse(String)
    case http(status: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .missingToken:
            return L("缺少 Notion API Token", "Missing Notion API token")
        case .invalidResponse(let message):
            return message
        case .http(let status, let message):
            return "Notion HTTP \(status): \(message)"
        }
    }
}

struct NotionPageReference: Sendable, Equatable {
    let pageID: String
    let recordID: String
}

struct NotionAPIClient: NotionHistoryClient {
    static let apiVersion = "2026-03-11"
    private let token: String
    private let transport: any NotionTransport
    private let baseURL: URL
    private let sleeper: @Sendable (UInt64) async -> Void

    init(
        token: String,
        transport: any NotionTransport = URLSessionNotionTransport(),
        baseURL: URL = URL(string: "https://api.notion.com/v1")!,
        sleeper: @escaping @Sendable (UInt64) async -> Void = { ns in try? await Task.sleep(nanoseconds: ns) }
    ) {
        self.token = token
        self.transport = transport
        self.baseURL = baseURL
        self.sleeper = sleeper
    }

    static func createPageBody(
        dataSourceID: String,
        record: HistoryRecord,
        includeRawText: Bool
    ) -> [String: Any] {
        [
            "parent": [
                "type": "data_source_id",
                "data_source_id": dataSourceID,
            ],
            "properties": NotionHistoryMapper.properties(for: record, includeRawText: includeRawText),
            "children": NotionHistoryMapper.children(for: record, includeRawText: includeRawText),
        ]
    }

    static func queryByVoxLogIDBody(recordID: String) -> [String: Any] {
        [
            "filter": [
                "property": NotionProperty.voxLogID,
                "rich_text": ["equals": recordID],
            ],
            "page_size": 1,
        ]
    }

    static func queryChangedBody(after cursor: String?, startCursor: String?) -> [String: Any] {
        var body: [String: Any] = [
            "sorts": [[
                "property": NotionProperty.syncUpdatedAt,
                "direction": "ascending",
            ]],
            "page_size": 100,
        ]
        if let cursor, !cursor.isEmpty {
            body["filter"] = [
                "property": NotionProperty.syncUpdatedAt,
                "date": ["after": cursor],
            ]
        }
        if let startCursor {
            body["start_cursor"] = startCursor
        }
        return body
    }

    func pageExists(dataSourceID: String, recordID: String) async throws -> Bool {
        let data = try await post(path: "/data_sources/\(dataSourceID)/query", body: Self.queryByVoxLogIDBody(recordID: recordID))
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let results = object["results"] as? [[String: Any]]
        else { throw NotionAPIError.invalidResponse("Invalid Notion query response") }
        return !results.isEmpty
    }

    func createPage(dataSourceID: String, record: HistoryRecord, includeRawText: Bool) async throws -> String {
        let data = try await post(
            path: "/pages",
            body: Self.createPageBody(dataSourceID: dataSourceID, record: record, includeRawText: includeRawText)
        )
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = object["id"] as? String
        else { throw NotionAPIError.invalidResponse("Invalid Notion create page response") }
        return id
    }

    func fetchChangedRecords(dataSourceID: String, after cursor: String?) async throws -> [HistoryRecord] {
        var startCursor: String?
        var records: [HistoryRecord] = []
        repeat {
            let data = try await post(
                path: "/data_sources/\(dataSourceID)/query",
                body: Self.queryChangedBody(after: cursor, startCursor: startCursor)
            )
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let results = object["results"] as? [[String: Any]]
            else { throw NotionAPIError.invalidResponse("Invalid Notion query response") }
            records.append(contentsOf: results.compactMap(Self.record(from:)))
            if object["has_more"] as? Bool == true {
                startCursor = object["next_cursor"] as? String
            } else {
                startCursor = nil
            }
        } while startCursor != nil
        return records
    }

    static func record(from page: [String: Any]) -> HistoryRecord? {
        guard let properties = page["properties"] as? [String: Any],
              let id = richText(properties[NotionProperty.voxLogID]),
              let createdAt = date(properties[NotionProperty.createdAt]),
              let finalText = richText(properties[NotionProperty.finalText])
        else { return nil }
        return HistoryRecord(
            id: id,
            createdAt: createdAt,
            durationSeconds: 0,
            rawText: richText(properties[NotionProperty.rawText]) ?? finalText,
            processingMode: nil,
            processedText: nil,
            finalText: finalText,
            status: select(properties[NotionProperty.status]) ?? "completed",
            characterCount: number(properties[NotionProperty.characterCount]) ?? finalText.count,
            asrProvider: richText(properties[NotionProperty.asrProvider]),
            agent: richText(properties[NotionProperty.agent]),
            deviceId: richText(properties[NotionProperty.deviceID]),
            syncUpdatedAt: dateString(properties[NotionProperty.syncUpdatedAt])
        )
    }

    private func post(path: String, body: [String: Any]) async throws -> Data {
        var request = URLRequest(url: url(for: path))
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue(Self.apiVersion, forHTTPHeaderField: "Notion-Version")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        return try await sendWithRetry(request)
    }

    private func url(for path: String) -> URL {
        path
            .split(separator: "/")
            .reduce(baseURL) { url, component in
                url.appendingPathComponent(String(component))
            }
    }

    private func sendWithRetry(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await transport.send(request)
        if response.statusCode == 429 {
            let retryAfter = UInt64(response.value(forHTTPHeaderField: "Retry-After") ?? "1") ?? 1
            await sleeper(retryAfter * 1_000_000_000)
            let (retryData, retryResponse) = try await transport.send(request)
            return try validate(data: retryData, response: retryResponse)
        }
        return try validate(data: data, response: response)
    }

    private func validate(data: Data, response: HTTPURLResponse) throws -> Data {
        guard (200..<300).contains(response.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Unknown error"
            throw NotionAPIError.http(status: response.statusCode, message: message)
        }
        return data
    }

    private static func richText(_ property: Any?) -> String? {
        guard let object = property as? [String: Any],
              let array = object["rich_text"] as? [[String: Any]]
        else { return nil }
        return array.compactMap { item in
            if let text = item["plain_text"] as? String { return text }
            if let text = item["text"] as? [String: Any] { return text["content"] as? String }
            return nil
        }.joined()
    }

    private static func date(_ property: Any?) -> Date? {
        guard let value = dateString(property) else { return nil }
        return ISO8601DateFormatter().date(from: value)
    }

    private static func dateString(_ property: Any?) -> String? {
        guard let object = property as? [String: Any],
              let date = object["date"] as? [String: Any]
        else { return nil }
        return date["start"] as? String
    }

    private static func number(_ property: Any?) -> Int? {
        guard let object = property as? [String: Any] else { return nil }
        if let number = object["number"] as? Int { return number }
        if let number = object["number"] as? Double { return Int(number) }
        return nil
    }

    private static func select(_ property: Any?) -> String? {
        guard let object = property as? [String: Any],
              let select = object["select"] as? [String: Any]
        else { return nil }
        return select["name"] as? String
    }
}
