import Foundation
import SQLite3
import os

/// Migrates data from VoxLog v2 (Python/Tauri) to VoxLog V3 (SwiftUI).
///
/// v2 database: ~/.voxlog2/history.db (table: voice_log)
/// v2 dictionary: ~/voxlog/terms.json
///
/// v3 database: ~/Library/Application Support/VoxLog/history.db (table: recognition_history)
/// v3 dictionary: HotwordStorage + SnippetStorage
enum V2Migrator {

    private static let logger = Logger(subsystem: "com.voxlog.migration", category: "V2Migrator")
    private static let migratedKey = "vl_v2MigrationDone"

    static var needsMigration: Bool {
        !UserDefaults.standard.bool(forKey: migratedKey) && hasV2Data
    }

    static var hasV2Data: Bool {
        let dbPath = NSHomeDirectory() + "/.voxlog2/history.db"
        return FileManager.default.fileExists(atPath: dbPath)
    }

    /// Run migration. Returns number of records migrated.
    static func migrate() async -> Int {
        guard needsMigration else { return 0 }

        var total = 0
        total += await migrateHistory()
        migrateDictionary()

        UserDefaults.standard.set(true, forKey: migratedKey)
        logger.info("V2 migration complete: \(total) records")
        return total
    }

    // MARK: - History Migration

    private static func migrateHistory() async -> Int {
        let v2Path = NSHomeDirectory() + "/.voxlog2/history.db"
        var v2db: OpaquePointer?
        guard sqlite3_open_v2(v2Path, &v2db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            logger.error("Cannot open v2 database")
            return 0
        }
        defer { sqlite3_close(v2db) }

        // Read v2 voice_log table
        let sql = """
        SELECT id, raw_text, polished_text, asr_provider, llm_provider,
               polished, duration_seconds, latency_ms, target_app, agent, env, created_at
        FROM voice_log ORDER BY created_at ASC;
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(v2db, sql, -1, &stmt, nil) == SQLITE_OK else {
            logger.error("Cannot prepare v2 query: \(String(cString: sqlite3_errmsg(v2db)))")
            return 0
        }
        defer { sqlite3_finalize(stmt) }

        let historyStore = HistoryStore()
        let iso = ISO8601DateFormatter()
        var count = 0

        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = columnStr(stmt, 0)
            let rawText = columnStr(stmt, 1)
            let polishedText = columnStr(stmt, 2)
            let asrProvider = columnStrOpt(stmt, 3)
            let duration = sqlite3_column_double(stmt, 6)
            let agent = columnStrOpt(stmt, 9) ?? "general"
            let createdAtStr = columnStr(stmt, 11)

            let createdAt = iso.date(from: createdAtStr) ?? Date()
            let finalText = polishedText.isEmpty ? rawText : polishedText

            guard !finalText.isEmpty else { continue }

            let record = HistoryRecord(
                id: id,
                createdAt: createdAt,
                durationSeconds: duration,
                rawText: rawText,
                processingMode: nil,
                processedText: polishedText.isEmpty ? nil : polishedText,
                finalText: finalText,
                status: "completed",
                characterCount: finalText.count,
                asrProvider: asrProvider,
                agent: agent
            )
            await historyStore.insert(record)
            count += 1
        }

        logger.info("Migrated \(count) history records from v2")
        return count
    }

    // MARK: - Dictionary Migration

    private static func migrateDictionary() {
        let termsPath = NSHomeDirectory() + "/voxlog/terms.json"
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: termsPath)),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            logger.info("No v2 terms.json found, skipping dictionary migration")
            return
        }

        // Migrate corrections → SnippetStorage
        if let corrections = json["corrections"] as? [String: String] {
            var snippets = SnippetStorage.load()
            for (trigger, value) in corrections {
                if !snippets.contains(where: { $0.trigger == trigger }) {
                    snippets.append((trigger: trigger, value: value))
                }
            }
            SnippetStorage.save(snippets)
            logger.info("Migrated \(corrections.count) corrections to SnippetStorage")
        }

        // Migrate preserve → HotwordStorage
        if let preserve = json["preserve"] as? [String] {
            var hotwords = HotwordStorage.load()
            for word in preserve {
                if !hotwords.contains(word) {
                    hotwords.append(word)
                }
            }
            HotwordStorage.save(hotwords)
            logger.info("Migrated \(preserve.count) hotwords to HotwordStorage")
        }
    }

    // MARK: - SQLite Helpers

    private static func columnStr(_ stmt: OpaquePointer?, _ idx: Int32) -> String {
        guard let cStr = sqlite3_column_text(stmt, idx) else { return "" }
        return String(cString: cStr)
    }

    private static func columnStrOpt(_ stmt: OpaquePointer?, _ idx: Int32) -> String? {
        guard sqlite3_column_type(stmt, idx) != SQLITE_NULL,
              let cStr = sqlite3_column_text(stmt, idx) else { return nil }
        let s = String(cString: cStr)
        return s.isEmpty ? nil : s
    }
}
