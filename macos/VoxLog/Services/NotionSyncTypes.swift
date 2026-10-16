import Foundation

enum NotionSyncDirection: String, CaseIterable, Identifiable {
    case uploadOnly = "upload_only"
    case downloadOnly = "download_only"
    case bidirectional

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .uploadOnly: return L("仅上传", "Upload only")
        case .downloadOnly: return L("仅下载", "Download only")
        case .bidirectional: return L("双向同步", "Bidirectional")
        }
    }
}

struct NotionSyncSettings: Equatable {
    static let enabledKey = "voxlog_notion_sync_enabled"
    static let dataSourceIDKey = "voxlog_notion_data_source_id"
    static let includeRawTextKey = "voxlog_notion_include_raw_text"
    static let directionKey = "voxlog_notion_sync_direction"

    var isEnabled: Bool
    var dataSourceID: String
    var includeRawText: Bool
    var direction: NotionSyncDirection

    static func load(from defaults: UserDefaults = .standard) -> NotionSyncSettings {
        NotionSyncSettings(
            isEnabled: defaults.bool(forKey: enabledKey),
            dataSourceID: defaults.string(forKey: dataSourceIDKey) ?? "",
            includeRawText: defaults.object(forKey: includeRawTextKey) as? Bool ?? false,
            direction: NotionSyncDirection(
                rawValue: defaults.string(forKey: directionKey) ?? ""
            ) ?? .bidirectional
        )
    }

    func save(to defaults: UserDefaults = .standard) {
        defaults.set(isEnabled, forKey: Self.enabledKey)
        defaults.set(dataSourceID.trimmingCharacters(in: .whitespacesAndNewlines), forKey: Self.dataSourceIDKey)
        defaults.set(includeRawText, forKey: Self.includeRawTextKey)
        defaults.set(direction.rawValue, forKey: Self.directionKey)
    }
}

struct NotionSyncResult: Sendable {
    let uploaded: Int
    let downloaded: Int
    let skipped: Int
    let error: String?

    var summary: String {
        if let error { return "Notion: \(error)" }
        var parts: [String] = []
        if uploaded > 0 { parts.append("↑\(uploaded)") }
        if downloaded > 0 { parts.append("↓\(downloaded)") }
        if skipped > 0 { parts.append("\(skipped) skipped") }
        return parts.isEmpty ? L("Notion 已是最新", "Notion is up to date") : parts.joined(separator: " · ")
    }
}

enum NotionProperty {
    static let title = "Name"
    static let voxLogID = "VoxLog ID"
    static let createdAt = "Created At"
    static let agent = "Agent"
    static let finalText = "Final Text"
    static let rawText = "Raw Text"
    static let asrProvider = "ASR Provider"
    static let deviceID = "Device ID"
    static let characterCount = "Character Count"
    static let syncUpdatedAt = "Sync Updated At"
    static let status = "Status"
}

enum NotionTextChunker {
    static let maxRichTextLength = 1900

    static func chunks(_ text: String, maxLength: Int = maxRichTextLength) -> [String] {
        guard !text.isEmpty else { return [""] }
        var result: [String] = []
        var remaining = text[...]
        while !remaining.isEmpty {
            let end = remaining.index(
                remaining.startIndex,
                offsetBy: min(maxLength, remaining.count),
                limitedBy: remaining.endIndex
            ) ?? remaining.endIndex
            result.append(String(remaining[..<end]))
            remaining = remaining[end...]
        }
        return result
    }

    static func propertyPreview(_ text: String) -> String {
        String(text.prefix(maxRichTextLength))
    }
}

struct NotionHistoryMapper {
    private static let iso = ISO8601DateFormatter()

    static func properties(for record: HistoryRecord, includeRawText: Bool) -> [String: Any] {
        var props: [String: Any] = [
            NotionProperty.title: title(record.finalText),
            NotionProperty.voxLogID: richText(record.id),
            NotionProperty.createdAt: date(record.createdAt),
            NotionProperty.agent: richText(record.agent ?? ""),
            NotionProperty.finalText: richText(NotionTextChunker.propertyPreview(record.finalText)),
            NotionProperty.asrProvider: richText(record.asrProvider ?? ""),
            NotionProperty.deviceID: richText(record.deviceId ?? DeviceIdentity.deviceId),
            NotionProperty.characterCount: ["number": record.characterCount ?? record.finalText.count],
            NotionProperty.syncUpdatedAt: dateString(record.syncUpdatedAt ?? iso.string(from: Date())),
            NotionProperty.status: select(record.status),
        ]
        if includeRawText {
            props[NotionProperty.rawText] = richText(NotionTextChunker.propertyPreview(record.rawText))
        }
        return props
    }

    static func children(for record: HistoryRecord, includeRawText: Bool) -> [[String: Any]] {
        var blocks = paragraphBlocks(title: L("最终文本", "Final text"), text: record.finalText)
        if includeRawText, record.rawText != record.finalText {
            blocks += paragraphBlocks(title: L("原始转写", "Raw transcript"), text: record.rawText)
        }
        return blocks
    }

    private static func title(_ text: String) -> [String: Any] {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let value = cleaned.isEmpty ? "VoxLog Message" : String(cleaned.prefix(80))
        return ["title": [["type": "text", "text": ["content": value]]]]
    }

    static func richText(_ text: String) -> [String: Any] {
        ["rich_text": [["type": "text", "text": ["content": NotionTextChunker.propertyPreview(text)]]]]
    }

    private static func date(_ date: Date) -> [String: Any] {
        ["date": ["start": iso.string(from: date)]]
    }

    private static func dateString(_ value: String) -> [String: Any] {
        ["date": ["start": value]]
    }

    private static func select(_ value: String) -> [String: Any] {
        ["select": ["name": value.isEmpty ? "completed" : value]]
    }

    private static func paragraphBlocks(title: String, text: String) -> [[String: Any]] {
        var blocks: [[String: Any]] = [[
            "object": "block",
            "type": "heading_3",
            "heading_3": ["rich_text": [["type": "text", "text": ["content": title]]]],
        ]]
        for chunk in NotionTextChunker.chunks(text) {
            blocks.append([
                "object": "block",
                "type": "paragraph",
                "paragraph": ["rich_text": [["type": "text", "text": ["content": chunk]]]],
            ])
        }
        return blocks
    }
}
