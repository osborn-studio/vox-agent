import Foundation
import os

/// Syncs VoxLog voice records to Obsidian vault as daily markdown logs.
/// Target: ~/obsidian-vault/06-osborn/voice-logs/YYYY-MM-DD-voice-log.md
actor ObsidianSync {

    static let shared = ObsidianSync()
    private let logger = Logger(subsystem: "com.voxlog.sync", category: "ObsidianSync")

    private var vaultPath: String {
        NSHomeDirectory() + "/obsidian-vault"
    }

    private var voiceLogsDir: String {
        vaultPath + "/06-osborn/voice-logs"
    }

    /// Sync recent records to Obsidian. Returns number of files written.
    func syncRecent(days: Int = 7) async -> Int {
        let historyStore = HistoryStore()
        let fm = FileManager.default

        // Check vault exists
        guard fm.fileExists(atPath: vaultPath) else {
            logger.warning("Obsidian vault not found at \(self.vaultPath)")
            return 0
        }

        // Create voice-logs directory
        try? fm.createDirectory(atPath: voiceLogsDir, withIntermediateDirectories: true)

        let calendar = Calendar.current
        let today = Date()
        var filesWritten = 0

        for dayOffset in 0..<days {
            guard let date = calendar.date(byAdding: .day, value: -dayOffset, to: today) else { continue }

            let dateStr = formatDate(date)
            let records = await fetchRecordsForDate(historyStore: historyStore, date: dateStr)
            guard !records.isEmpty else { continue }

            let markdown = buildMarkdown(date: dateStr, records: records)
            let filePath = "\(voiceLogsDir)/\(dateStr)-voice-log.md"

            do {
                try markdown.write(toFile: filePath, atomically: true, encoding: .utf8)
                filesWritten += 1
                logger.info("Synced \(records.count) records to \(filePath)")
            } catch {
                logger.error("Failed to write \(filePath): \(error)")
            }
        }

        // Update index file
        await updateIndex()

        logger.info("Obsidian sync complete: \(filesWritten) files")
        return filesWritten
    }

    // MARK: - Helpers

    private func formatDate(_ date: Date) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"
        return fmt.string(from: date)
    }

    private func fetchRecordsForDate(historyStore: HistoryStore, date: String) async -> [HistoryRecord] {
        let all = await historyStore.fetchAll(limit: 1000)
        let iso = ISO8601DateFormatter()
        return all.filter { record in
            let recordDate = iso.string(from: record.createdAt)
            return recordDate.hasPrefix(date)
        }
    }

    private func buildMarkdown(date: String, records: [HistoryRecord]) -> String {
        let totalDuration = records.reduce(0.0) { $0 + $1.durationSeconds }
        let agentGroups = Dictionary(grouping: records) { $0.agent ?? "general" }

        var md = """
        ---
        date: \(date)
        recordings: \(records.count)
        duration_min: \(String(format: "%.1f", totalDuration / 60))
        tags: [voxlog, voice-log]
        ---

        # Voice Log \(date)

        > \(records.count) recordings, \(String(format: "%.1f", totalDuration / 60)) min total


        """

        // Group by agent
        for (agent, agentRecords) in agentGroups.sorted(by: { $0.key < $1.key }) {
            md += "## \(agent)\n\n"
            for record in agentRecords.sorted(by: { $0.createdAt < $1.createdAt }) {
                let time = formatTime(record.createdAt)
                let dur = String(format: "%.1f", record.durationSeconds)
                let provider = record.asrProvider ?? ""
                md += "### \(time) (\(dur)s, \(provider))\n\n"
                md += "\(record.finalText)\n\n"
                if let raw = record.rawText as String?,
                   raw != record.finalText, !raw.isEmpty {
                    md += "> Raw: \(raw)\n\n"
                }
            }
        }

        return md
    }

    private func formatTime(_ date: Date) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm"
        return fmt.string(from: date)
    }

    // MARK: - Cross-Machine Sync (M5)

    /// Sync shared data (agents, dictionary, settings) via Obsidian vault.
    /// Two Macs running VoxLog can share data through their synced Obsidian vault.
    func syncSharedData() async {
        let fm = FileManager.default
        let syncDir = vaultPath + "/06-osborn/voxlog-sync"
        try? fm.createDirectory(atPath: syncDir, withIntermediateDirectories: true)

        // Export local data
        await exportAgents(to: syncDir)
        exportDictionary(to: syncDir)
        exportSettings(to: syncDir)

        // Import remote data (from the other machine's sync)
        await importAgents(from: syncDir)
        importDictionary(from: syncDir)

        logger.info("Cross-machine sync complete via \(syncDir)")
    }

    private func exportAgents(to dir: String) async {
        let agentStore = AgentStore()
        let agents = await agentStore.allAgents()
        let hostname = ProcessInfo.processInfo.hostName
        let filePath = "\(dir)/agents-\(hostname).json"
        if let data = try? JSONEncoder().encode(agents) {
            try? data.write(to: URL(fileURLWithPath: filePath), options: .atomic)
        }
    }

    private func importAgents(from dir: String) async {
        let fm = FileManager.default
        let hostname = ProcessInfo.processInfo.hostName
        guard let files = try? fm.contentsOfDirectory(atPath: dir) else { return }

        let agentStore = AgentStore()
        let localAgents = await agentStore.allAgents()
        let localIds = Set(localAgents.map(\.id))

        for file in files where file.hasPrefix("agents-") && file.hasSuffix(".json") && !file.contains(hostname) {
            let filePath = "\(dir)/\(file)"
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: filePath)),
                  let remoteAgents = try? JSONDecoder().decode([Agent].self, from: data)
            else { continue }

            // Merge: add remote agents not in local
            for agent in remoteAgents where !localIds.contains(agent.id) && agent.agentType == .userCreated {
                let _ = await agentStore.createAgent(name: agent.name, emoji: agent.emoji)
                logger.info("Imported agent from remote: \(agent.name)")
            }
        }
    }

    private func exportDictionary(to dir: String) {
        let hostname = ProcessInfo.processInfo.hostName
        // Hotwords
        let hotwords = HotwordStorage.load()
        let hotwordsPath = "\(dir)/hotwords-\(hostname).json"
        if let data = try? JSONEncoder().encode(hotwords) {
            try? data.write(to: URL(fileURLWithPath: hotwordsPath), options: .atomic)
        }
        // Snippets
        let snippets = SnippetStorage.load()
        let snippetPairs = snippets.map { ["trigger": $0.trigger, "value": $0.value] }
        let snippetsPath = "\(dir)/snippets-\(hostname).json"
        if let data = try? JSONSerialization.data(withJSONObject: snippetPairs) {
            try? data.write(to: URL(fileURLWithPath: snippetsPath), options: .atomic)
        }
    }

    private func importDictionary(from dir: String) {
        let fm = FileManager.default
        let hostname = ProcessInfo.processInfo.hostName
        guard let files = try? fm.contentsOfDirectory(atPath: dir) else { return }

        // Import hotwords from other machines
        var localHotwords = HotwordStorage.load()
        for file in files where file.hasPrefix("hotwords-") && file.hasSuffix(".json") && !file.contains(hostname) {
            let filePath = "\(dir)/\(file)"
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: filePath)),
                  let remoteWords = try? JSONDecoder().decode([String].self, from: data)
            else { continue }
            for word in remoteWords where !localHotwords.contains(word) {
                localHotwords.append(word)
            }
        }
        HotwordStorage.save(localHotwords)

        // Import snippets from other machines
        var localSnippets = SnippetStorage.load()
        let localTriggers = Set(localSnippets.map(\.trigger))
        for file in files where file.hasPrefix("snippets-") && file.hasSuffix(".json") && !file.contains(hostname) {
            let filePath = "\(dir)/\(file)"
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: filePath)),
                  let remoteSnippets = try? JSONSerialization.jsonObject(with: data) as? [[String: String]]
            else { continue }
            for snippet in remoteSnippets {
                if let trigger = snippet["trigger"], let value = snippet["value"],
                   !localTriggers.contains(trigger) {
                    localSnippets.append((trigger: trigger, value: value))
                }
            }
        }
        SnippetStorage.save(localSnippets)
    }

    private func exportSettings(to dir: String) {
        let hostname = ProcessInfo.processInfo.hostName
        let settings: [String: String] = [
            "hostname": hostname,
            "asr_provider": KeychainService.selectedASRProvider.rawValue,
            "llm_provider": KeychainService.selectedLLMProvider.rawValue,
            "last_sync": ISO8601DateFormatter().string(from: Date()),
        ]
        let filePath = "\(dir)/settings-\(hostname).json"
        if let data = try? JSONSerialization.data(withJSONObject: settings) {
            try? data.write(to: URL(fileURLWithPath: filePath), options: .atomic)
        }
    }

    private func updateIndex() async {
        let fm = FileManager.default
        guard let files = try? fm.contentsOfDirectory(atPath: voiceLogsDir) else { return }

        let logFiles = files.filter { $0.hasSuffix("-voice-log.md") }.sorted().reversed()

        var index = """
        ---
        tags: [voxlog, index]
        ---

        # VoxLog Voice Logs Index

        | Date | File |
        |------|------|

        """

        for file in logFiles {
            let date = String(file.prefix(10))
            index += "| \(date) | [[\(file)]] |\n"
        }

        let indexPath = "\(voiceLogsDir)/VoxLog-Index.md"
        try? index.write(toFile: indexPath, atomically: true, encoding: .utf8)
    }
}
