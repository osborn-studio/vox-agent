import Foundation

actor NotionSyncEngine {
    static let shared = NotionSyncEngine()

    private struct Manifest: Codable, Equatable {
        var lastUploadCursor: String?
        var lastDownloadCursor: String?
    }

    private let historyStore: HistoryStore
    private let settingsOverride: NotionSyncSettings?
    private let manifestURLOverride: URL?

    init(
        historyStore: HistoryStore = HistoryStore(),
        settings: NotionSyncSettings? = nil,
        manifestURL: URL? = nil
    ) {
        self.historyStore = historyStore
        self.settingsOverride = settings
        self.manifestURLOverride = manifestURL
    }

    func sync(client injectedClient: (any NotionHistoryClient)? = nil) async -> NotionSyncResult {
        let settings = settingsOverride ?? NotionSyncSettings.load()
        guard settings.isEnabled else {
            return NotionSyncResult(uploaded: 0, downloaded: 0, skipped: 0, error: L("Notion 同步未开启", "Notion sync is disabled"))
        }
        guard !settings.dataSourceID.isEmpty else {
            return NotionSyncResult(uploaded: 0, downloaded: 0, skipped: 0, error: L("缺少 Notion Data Source ID", "Missing Notion data source ID"))
        }

        let client: any NotionHistoryClient
        if let injectedClient {
            client = injectedClient
        } else {
            guard let token = KeychainService.loadNotionToken() else {
                return NotionSyncResult(uploaded: 0, downloaded: 0, skipped: 0, error: L("缺少 Notion API Token", "Missing Notion API token"))
            }
            client = NotionAPIClient(token: token)
        }

        var manifest = loadManifest()
        var uploaded = 0
        var downloaded = 0
        var skipped = 0

        do {
            if settings.direction == .uploadOnly || settings.direction == .bidirectional {
                let result = try await upload(settings: settings, client: client, manifest: &manifest)
                uploaded += result.uploaded
                skipped += result.skipped
            }
            if settings.direction == .downloadOnly || settings.direction == .bidirectional {
                let result = try await download(settings: settings, client: client, manifest: &manifest)
                downloaded += result.downloaded
                skipped += result.skipped
            }
            saveManifest(manifest)
            return NotionSyncResult(uploaded: uploaded, downloaded: downloaded, skipped: skipped, error: nil)
        } catch {
            return NotionSyncResult(uploaded: uploaded, downloaded: downloaded, skipped: skipped, error: error.localizedDescription)
        }
    }

    private func upload(
        settings: NotionSyncSettings,
        client: any NotionHistoryClient,
        manifest: inout Manifest
    ) async throws -> (uploaded: Int, skipped: Int) {
        await historyStore.backfillDeviceId()
        let records = await historyStore.fetchUpdatedSince(manifest.lastUploadCursor)
        var uploaded = 0
        var skipped = 0
        var latestCursor = manifest.lastUploadCursor

        for record in records {
            if try await client.pageExists(dataSourceID: settings.dataSourceID, recordID: record.id) {
                skipped += 1
            } else {
                _ = try await client.createPage(
                    dataSourceID: settings.dataSourceID,
                    record: record,
                    includeRawText: settings.includeRawText
                )
                uploaded += 1
            }
            latestCursor = maxCursor(latestCursor, record.syncUpdatedAt ?? ISO8601DateFormatter().string(from: record.createdAt))
        }
        manifest.lastUploadCursor = latestCursor
        return (uploaded, skipped)
    }

    private func download(
        settings: NotionSyncSettings,
        client: any NotionHistoryClient,
        manifest: inout Manifest
    ) async throws -> (downloaded: Int, skipped: Int) {
        let records = try await client.fetchChangedRecords(
            dataSourceID: settings.dataSourceID,
            after: manifest.lastDownloadCursor
        )
        var downloaded = 0
        var skipped = 0
        var latestCursor = manifest.lastDownloadCursor

        for record in records {
            if await historyStore.mergeRemote(record) {
                downloaded += 1
            } else {
                skipped += 1
            }
            latestCursor = maxCursor(latestCursor, record.syncUpdatedAt ?? ISO8601DateFormatter().string(from: record.createdAt))
        }
        manifest.lastDownloadCursor = latestCursor
        return (downloaded, skipped)
    }

    private func maxCursor(_ current: String?, _ candidate: String) -> String {
        guard let current, !current.isEmpty else { return candidate }
        return max(current, candidate)
    }

    private var manifestURL: URL {
        if let manifestURLOverride { return manifestURLOverride }
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("VoxLog", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("notion-sync-manifest.json")
    }

    private func loadManifest() -> Manifest {
        guard let data = try? Data(contentsOf: manifestURL),
              let manifest = try? JSONDecoder().decode(Manifest.self, from: data)
        else { return Manifest() }
        return manifest
    }

    private func saveManifest(_ manifest: Manifest) {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(manifest) else { return }
        try? data.write(to: manifestURL, options: .atomic)
    }
}
