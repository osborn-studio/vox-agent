import Foundation
import os

/// Git-based incremental sync engine for VoxLog cross-device data.
///
/// Design principles:
/// - Each device writes ONLY to `{syncDir}/{myDevice}/` — no cross-write
/// - Import reads from other devices' folders
/// - Git handles incremental transport (only changed files travel over network)
/// - Daily JSONL files for history — one file per day, append-only
/// - Local manifest tracks per-remote-device import cursors
///
/// Repo layout:
/// ```
/// {device}/
/// ├── manifest.json
/// ├── history/
/// │   └── 2026-04-09.jsonl
/// ├── agents.json
/// ├── snippets.json
/// └── hotwords.json
/// ```
actor GitSyncEngine {

    static let shared = GitSyncEngine()
    private let logger = Logger(subsystem: "com.voxlog.sync", category: "GitSync")

    // MARK: - Configuration

    private static let repoURLKey = "voxlog_sync_repo_url"
    private static let localPathKey = "voxlog_sync_local_path"

    static var repoURL: String {
        get { UserDefaults.standard.string(forKey: repoURLKey) ?? "https://github.com/gilosbornhello/voxlog-sync.git" }
        set { UserDefaults.standard.set(newValue, forKey: repoURLKey) }
    }

    static var localPath: String {
        get { UserDefaults.standard.string(forKey: localPathKey) ?? (NSHomeDirectory() + "/Shared/voxlog-sync") }
        set { UserDefaults.standard.set(newValue, forKey: localPathKey) }
    }

    private var syncDir: String { Self.localPath }
    private var myDir: String { "\(syncDir)/\(DeviceIdentity.shortName)" }

    // MARK: - Sync Result

    struct SyncResult: Sendable {
        let exported: Int
        let imported: Int
        let agentsMerged: Int
        let snippetsMerged: Int
        let duration: Double
        let error: String?

        var summary: String {
            if let error { return "Error: \(error)" }
            var parts: [String] = []
            if exported > 0 { parts.append("↑\(exported)") }
            if imported > 0 { parts.append("↓\(imported)") }
            if agentsMerged > 0 { parts.append("+\(agentsMerged) agents") }
            if snippetsMerged > 0 { parts.append("+\(snippetsMerged) snippets") }
            if parts.isEmpty { return "已是最新" }
            return parts.joined(separator: " · ")
        }
    }

    // MARK: - Local Manifest (not committed to git — tracks import state)

    private struct LocalManifest: Codable {
        var lastExportDate: String?                  // YYYY-MM-DD of last export
        var importCursors: [String: String]           // [remoteDevice: lastImportedDate]
    }

    private var localManifestPath: String {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("VoxLog", isDirectory: true).path
        return "\(dir)/sync-manifest.json"
    }

    private func loadLocalManifest() -> LocalManifest {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: localManifestPath)),
              let m = try? JSONDecoder().decode(LocalManifest.self, from: data) else {
            return LocalManifest(importCursors: [:])
        }
        return m
    }

    private func saveLocalManifest(_ m: LocalManifest) {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(m) else { return }
        try? data.write(to: URL(fileURLWithPath: localManifestPath), options: .atomic)
    }

    // MARK: - Remote Manifest (committed to git — visible to other devices)

    private struct RemoteManifest: Codable {
        var deviceId: String
        var deviceName: String
        var lastSyncAt: String
        var totalRecords: Int
    }

    // MARK: - Full Sync

    func sync() async -> SyncResult {
        let start = ContinuousClock.now

        // 1. Ensure repo is cloned
        if let err = ensureCloned() {
            return SyncResult(exported: 0, imported: 0, agentsMerged: 0, snippetsMerged: 0,
                              duration: 0, error: err)
        }

        // Previous runs may have written manifest/snippet/hotword files without a history export.
        // Commit our own device directory before rebasing so local metadata cannot block pull.
        if let err = commitLocalDeviceChangesIfNeeded(message: "sync: \(DeviceIdentity.shortName) metadata") {
            return SyncResult(exported: 0, imported: 0, agentsMerged: 0, snippetsMerged: 0,
                              duration: 0, error: err)
        }

        // 2. Git pull (get latest from all devices)
        if let err = gitPull() {
            return SyncResult(exported: 0, imported: 0, agentsMerged: 0, snippetsMerged: 0,
                              duration: 0, error: err)
        }

        // 3. Backfill device_id on old records
        let historyStore = HistoryStore()
        await historyStore.backfillDeviceId()

        // 4. Load local manifest
        var manifest = loadLocalManifest()

        // 5. Export local data (write to myDir/)
        let exported = await exportHistory(historyStore: historyStore, lastExportDate: manifest.lastExportDate)
        _ = await exportAgents()
        exportSnippets()
        exportHotwords()
        writeRemoteManifest(totalRecords: await historyStore.count())

        // 6. Import from other devices
        let (imported, agentsMerged, snippetsMerged) = await importAll(
            historyStore: historyStore, manifest: &manifest
        )

        // 7. Update local manifest
        let fmt = DateFormatter(); fmt.dateFormat = "yyyy-MM-dd"
        manifest.lastExportDate = fmt.string(from: Date())
        saveLocalManifest(manifest)

        // 8. Git commit + push if any tracked sync artifact changed.
        gitCommitAndPush(message: "sync: \(DeviceIdentity.shortName) ↑\(exported) records")

        let elapsed = ContinuousClock.now - start
        let ms = elapsed.components.seconds * 1000 + elapsed.components.attoseconds / 1_000_000_000_000_000
        logger.info("Sync done in \(ms)ms: ↑\(exported) ↓\(imported)")

        return SyncResult(
            exported: exported, imported: imported,
            agentsMerged: agentsMerged, snippetsMerged: snippetsMerged,
            duration: Double(ms) / 1000.0, error: nil
        )
    }

    // MARK: - History Export (daily JSONL, append-only)

    private func exportHistory(historyStore: HistoryStore, lastExportDate: String?) async -> Int {
        let fm = FileManager.default
        let historyDir = "\(myDir)/history"
        try? fm.createDirectory(atPath: historyDir, withIntermediateDirectories: true)

        // Find dates with data since last export
        let dates = await historyStore.distinctDates(since: lastExportDate)
        guard !dates.isEmpty else { return 0 }

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        var totalExported = 0

        for dateStr in dates {
            let records = await historyStore.fetchForDate(dateStr)
            guard !records.isEmpty else { continue }

            let filePath = "\(historyDir)/\(dateStr).jsonl"

            // Read existing IDs in this daily file to avoid duplicates
            var existingIds = Set<String>()
            if let existing = try? String(contentsOfFile: filePath, encoding: .utf8) {
                for line in existing.split(separator: "\n") {
                    if let data = line.data(using: .utf8),
                       let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let id = obj["id"] as? String {
                        existingIds.insert(id)
                    }
                }
            }

            // Append only new records
            var newLines = ""
            var count = 0
            for record in records where !existingIds.contains(record.id) {
                if let data = try? encoder.encode(record),
                   let line = String(data: data, encoding: .utf8) {
                    newLines += line + "\n"
                    count += 1
                }
            }

            if count > 0 {
                if let handle = FileHandle(forWritingAtPath: filePath) {
                    handle.seekToEndOfFile()
                    handle.write(newLines.data(using: .utf8)!)
                    handle.closeFile()
                } else {
                    try? newLines.write(toFile: filePath, atomically: true, encoding: .utf8)
                }
                totalExported += count
            }
        }

        return totalExported
    }

    // MARK: - History Import (incremental by date cursor per remote device)

    private func importHistory(historyStore: HistoryStore, remoteDevice: String,
                               cursor: String?, manifest: inout LocalManifest) async -> Int {
        let historyDir = "\(syncDir)/\(remoteDevice)/history"
        let fm = FileManager.default
        guard let files = try? fm.contentsOfDirectory(atPath: historyDir) else { return 0 }

        // Only process .jsonl files with dates >= cursor
        let jsonlFiles = files.filter { $0.hasSuffix(".jsonl") }.sorted()
        let relevantFiles: [String]
        if let cursor {
            relevantFiles = jsonlFiles.filter { $0 >= "\(cursor).jsonl" }
        } else {
            relevantFiles = jsonlFiles
        }

        guard !relevantFiles.isEmpty else { return 0 }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        var imported = 0
        var latestDate: String?

        for file in relevantFiles {
            let filePath = "\(historyDir)/\(file)"
            guard let content = try? String(contentsOfFile: filePath, encoding: .utf8) else { continue }

            for line in content.split(separator: "\n") {
                guard let data = line.data(using: .utf8),
                      let record = try? decoder.decode(HistoryRecord.self, from: data) else { continue }
                if await historyStore.mergeRemote(record) {
                    imported += 1
                }
            }

            // Track the date from filename (e.g. "2026-04-09.jsonl" → "2026-04-09")
            latestDate = String(file.dropLast(6)) // remove ".jsonl"
        }

        // Update cursor for this remote device
        if let latestDate {
            manifest.importCursors[remoteDevice] = latestDate
        }

        return imported
    }

    // MARK: - Agent / Snippet / Hotword Export

    private func exportAgents() async -> Bool {
        let fm = FileManager.default
        try? fm.createDirectory(atPath: myDir, withIntermediateDirectories: true)
        let agentStore = AgentStore()
        let agents = await agentStore.allAgents()
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(agents) else { return false }
        let path = "\(myDir)/agents.json"
        let existing = try? Data(contentsOf: URL(fileURLWithPath: path))
        guard existing != data else { return false } // No change
        try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
        return true
    }

    private func exportSnippets() {
        let fm = FileManager.default
        try? fm.createDirectory(atPath: myDir, withIntermediateDirectories: true)
        let snippets = SnippetStorage.load()
        let pairs = snippets.map { ["trigger": $0.trigger, "value": $0.value] }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted]
        guard let data = try? encoder.encode(pairs) else { return }
        try? data.write(to: URL(fileURLWithPath: "\(myDir)/snippets.json"), options: .atomic)
    }

    private func exportHotwords() {
        let fm = FileManager.default
        try? fm.createDirectory(atPath: myDir, withIntermediateDirectories: true)
        let hotwords = HotwordStorage.load()
        guard let data = try? JSONEncoder().encode(hotwords) else { return }
        try? data.write(to: URL(fileURLWithPath: "\(myDir)/hotwords.json"), options: .atomic)
    }

    private func writeRemoteManifest(totalRecords: Int) {
        let m = RemoteManifest(
            deviceId: DeviceIdentity.deviceId,
            deviceName: DeviceIdentity.deviceName,
            lastSyncAt: ISO8601DateFormatter().string(from: Date()),
            totalRecords: totalRecords
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(m) else { return }
        try? data.write(to: URL(fileURLWithPath: "\(myDir)/manifest.json"), options: .atomic)
    }

    // MARK: - Import All Remote Devices

    private func importAll(historyStore: HistoryStore,
                           manifest: inout LocalManifest) async -> (history: Int, agents: Int, snippets: Int) {
        let fm = FileManager.default
        let myDevice = DeviceIdentity.shortName
        guard let dirs = try? fm.contentsOfDirectory(atPath: syncDir) else { return (0, 0, 0) }

        var totalHistory = 0, totalAgents = 0, totalSnippets = 0

        for dir in dirs {
            // Skip my own directory, hidden files, and non-directories
            guard dir != myDevice, !dir.hasPrefix("."), dir != "README.md" else { continue }
            let dirPath = "\(syncDir)/\(dir)"
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: dirPath, isDirectory: &isDir), isDir.boolValue else { continue }

            // Import history with cursor
            let cursor = manifest.importCursors[dir]
            let historyImported = await importHistory(
                historyStore: historyStore, remoteDevice: dir,
                cursor: cursor, manifest: &manifest
            )
            totalHistory += historyImported

            // Import agents
            totalAgents += await importAgents(from: dirPath)

            // Import snippets
            totalSnippets += importSnippets(from: dirPath)

            // Import hotwords
            importHotwords(from: dirPath)
        }

        return (totalHistory, totalAgents, totalSnippets)
    }

    private func importAgents(from deviceDir: String) async -> Int {
        let path = "\(deviceDir)/agents.json"
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let remoteAgents = try? JSONDecoder().decode([Agent].self, from: data) else { return 0 }

        let agentStore = AgentStore()
        let localIds = Set((await agentStore.allAgents()).map(\.id))
        var merged = 0
        for agent in remoteAgents where !localIds.contains(agent.id) && agent.agentType == .userCreated {
            let _ = await agentStore.createAgent(name: agent.name, emoji: agent.emoji)
            merged += 1
        }
        return merged
    }

    private func importSnippets(from deviceDir: String) -> Int {
        let path = "\(deviceDir)/snippets.json"
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let remote = try? JSONDecoder().decode([[String: String]].self, from: data) else { return 0 }

        var local = SnippetStorage.load()
        let localTriggers = Set(local.map { $0.trigger.lowercased() })
        var merged = 0
        for s in remote {
            if let t = s["trigger"], let v = s["value"], !localTriggers.contains(t.lowercased()) {
                local.append((trigger: t, value: v))
                merged += 1
            }
        }
        if merged > 0 { SnippetStorage.save(local) }
        return merged
    }

    private func importHotwords(from deviceDir: String) {
        let path = "\(deviceDir)/hotwords.json"
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let remote = try? JSONDecoder().decode([String].self, from: data) else { return }
        var local = HotwordStorage.load()
        let localSet = Set(local)
        var changed = false
        for w in remote where !localSet.contains(w) {
            local.append(w)
            changed = true
        }
        if changed { HotwordStorage.save(local) }
    }

    // MARK: - Git Operations

    private func ensureCloned() -> String? {
        let fm = FileManager.default
        if fm.fileExists(atPath: "\(syncDir)/.git") { return nil }

        // Parent dir
        let parent = (syncDir as NSString).deletingLastPathComponent
        try? fm.createDirectory(atPath: parent, withIntermediateDirectories: true)

        let (_, err, code) = shell("git clone \(Self.repoURL) \(syncDir)")
        if code != 0 { return "Clone failed: \(err)" }
        return nil
    }

    private func gitPull() -> String? {
        let (_, err, code) = shell("cd \(syncDir) && git pull --rebase --quiet 2>&1")
        if code != 0 { return "Pull failed: \(err)" }
        return nil
    }

    private func commitLocalDeviceChangesIfNeeded(message: String) -> String? {
        let device = DeviceIdentity.shortName
        let (dirtyFiles, _, statusCode) = shell("cd \(syncDir) && git status --porcelain")
        guard statusCode == 0 else { return "Status failed" }
        let dirtyLines = dirtyFiles.split(separator: "\n").map(String.init)
        guard !dirtyLines.isEmpty else { return nil }

        let allowedPrefix = "\(device)/"
        let disallowed = dirtyLines.filter { line in
            let path = String(line.dropFirst(3))
            return path != "README.md" && !path.hasPrefix(allowedPrefix)
        }
        if !disallowed.isEmpty {
            return "Pull blocked by local changes outside this device: \(disallowed.joined(separator: ", "))"
        }

        gitCommitAndPush(message: message)
        return nil
    }

    private func gitCommitAndPush(message: String) {
        let device = DeviceIdentity.shortName
        // Only stage our own directory
        let _ = shell("cd \(syncDir) && git add \(device)/ && git add README.md")
        let (_, _, commitCode) = shell("cd \(syncDir) && git diff --cached --quiet")
        if commitCode != 0 {
            // There are staged changes
            let _ = shell("cd \(syncDir) && git commit -m '\(message)'")
            let _ = shell("cd \(syncDir) && git push --quiet 2>&1")
            logger.info("Pushed: \(message)")
        }
    }

    @discardableResult
    private func shell(_ command: String) -> (stdout: String, stderr: String, code: Int32) {
        let process = Process()
        let outPipe = Pipe(), errPipe = Pipe()
        process.launchPath = "/bin/zsh"
        process.arguments = ["-c", command]
        process.standardOutput = outPipe
        process.standardError = errPipe
        process.environment = ProcessInfo.processInfo.environment
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            return ("", error.localizedDescription, -1)
        }
        let out = String(data: outPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let err = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        return (out.trimmingCharacters(in: .whitespacesAndNewlines),
                err.trimmingCharacters(in: .whitespacesAndNewlines),
                process.terminationStatus)
    }

    // MARK: - Device Discovery

    struct DeviceInfo: Sendable {
        let name: String
        let deviceId: String
        let lastSync: String
        let totalRecords: Int
        let isSelf: Bool
    }

    func listDevices() -> [DeviceInfo] {
        let fm = FileManager.default
        let myDevice = DeviceIdentity.shortName
        guard let dirs = try? fm.contentsOfDirectory(atPath: syncDir) else { return [] }

        var devices: [DeviceInfo] = []
        for dir in dirs.sorted() {
            let manifestPath = "\(syncDir)/\(dir)/manifest.json"
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: manifestPath)),
                  let m = try? JSONDecoder().decode(RemoteManifest.self, from: data) else { continue }
            devices.append(DeviceInfo(
                name: m.deviceName, deviceId: m.deviceId,
                lastSync: m.lastSyncAt, totalRecords: m.totalRecords,
                isSelf: dir == myDevice
            ))
        }
        return devices
    }
}
