import Foundation
import SQLite3

extension Notification.Name {
    static let historyStoreDidChange = Notification.Name("VoxLog.historyStoreDidChange")
}

actor HistoryStore {

    private var db: OpaquePointer?

    init(path: String? = nil) {
        let dbPath: String
        if let path {
            dbPath = path
        } else {
            let appSupport = FileManager.default.urls(
                for: .applicationSupportDirectory, in: .userDomainMask
            ).first!.appendingPathComponent("VoxLog", isDirectory: true)
            try? FileManager.default.createDirectory(at: appSupport, withIntermediateDirectories: true)
            dbPath = appSupport.appendingPathComponent("history.db").path
        }

        if sqlite3_open(dbPath, &db) == SQLITE_OK {
            let sql = """
            CREATE TABLE IF NOT EXISTS recognition_history (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                duration_seconds REAL,
                raw_text TEXT NOT NULL,
                processing_mode TEXT,
                processed_text TEXT,
                final_text TEXT NOT NULL,
                status TEXT NOT NULL,
                character_count INTEGER,
                asr_provider TEXT
            );
            """
            sqlite3_exec(db, sql, nil, nil, nil)

            // Migrations (ALTER TABLE silently fails if column already exists)
            sqlite3_exec(db, "ALTER TABLE recognition_history ADD COLUMN character_count INTEGER;", nil, nil, nil)
            sqlite3_exec(db, "ALTER TABLE recognition_history ADD COLUMN asr_provider TEXT;", nil, nil, nil)
            sqlite3_exec(db, "ALTER TABLE recognition_history ADD COLUMN agent TEXT;", nil, nil, nil)
            sqlite3_exec(db, "ALTER TABLE recognition_history ADD COLUMN device_id TEXT;", nil, nil, nil)
            sqlite3_exec(db, "ALTER TABLE recognition_history ADD COLUMN sync_updated_at TEXT;", nil, nil, nil)

            // Indexes
            sqlite3_exec(db, "CREATE INDEX IF NOT EXISTS idx_recognition_history_agent ON recognition_history(agent);", nil, nil, nil)
            sqlite3_exec(db, "CREATE INDEX IF NOT EXISTS idx_rh_sync ON recognition_history(sync_updated_at);", nil, nil, nil)
            sqlite3_exec(db, "CREATE INDEX IF NOT EXISTS idx_rh_created_date ON recognition_history(created_at);", nil, nil, nil)
        }
    }

    // MARK: - CRUD

    func insert(_ record: HistoryRecord) {
        let sql = """
        INSERT OR REPLACE INTO recognition_history
        (id, created_at, duration_seconds, raw_text, processing_mode, processed_text,
         final_text, status, character_count, asr_provider, agent, device_id, sync_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        defer { sqlite3_finalize(stmt) }

        let iso = ISO8601DateFormatter()
        let now = iso.string(from: Date())
        bind(stmt, 1, record.id)
        bind(stmt, 2, iso.string(from: record.createdAt))
        sqlite3_bind_double(stmt, 3, record.durationSeconds)
        bind(stmt, 4, record.rawText)
        bindOptional(stmt, 5, record.processingMode)
        bindOptional(stmt, 6, record.processedText)
        bind(stmt, 7, record.finalText)
        bind(stmt, 8, record.status)
        if let count = record.characterCount {
            sqlite3_bind_int(stmt, 9, Int32(count))
        } else {
            sqlite3_bind_null(stmt, 9)
        }
        bindOptional(stmt, 10, record.asrProvider)
        bindOptional(stmt, 11, record.agent)
        bind(stmt, 12, record.deviceId ?? DeviceIdentity.deviceId)
        bind(stmt, 13, record.syncUpdatedAt ?? now)
        if sqlite3_step(stmt) == SQLITE_DONE {
            postDidChangeNotification()
        }
    }

    func fetchAll(limit: Int? = nil, offset: Int = 0) -> [HistoryRecord] {
        let sql: String
        if let limit {
            sql = "SELECT * FROM recognition_history ORDER BY created_at DESC LIMIT \(limit) OFFSET \(offset);"
        } else {
            sql = "SELECT * FROM recognition_history ORDER BY created_at DESC;"
        }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        return readRows(stmt)
    }

    func fetchUpdatedSince(_ cursor: String?, limit: Int = 500) -> [HistoryRecord] {
        let sql: String
        if cursor?.isEmpty == false {
            sql = """
            SELECT * FROM recognition_history
            WHERE COALESCE(sync_updated_at, created_at) > ?
            ORDER BY COALESCE(sync_updated_at, created_at) ASC
            LIMIT \(limit);
            """
        } else {
            sql = """
            SELECT * FROM recognition_history
            ORDER BY COALESCE(sync_updated_at, created_at) ASC
            LIMIT \(limit);
            """
        }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        if let cursor, !cursor.isEmpty {
            bind(stmt, 1, cursor)
        }
        return readRows(stmt)
    }

    /// Fetch records for a specific agent, newest first.
    func fetchByAgent(_ agentId: String, limit: Int = 200) -> [HistoryRecord] {
        let sql = "SELECT * FROM recognition_history WHERE agent = ? ORDER BY created_at DESC LIMIT \(limit);"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        bind(stmt, 1, agentId)
        return readRows(stmt)
    }

    /// Get message count per agent.
    func agentCounts() -> [String: Int] {
        let sql = "SELECT agent, COUNT(*) FROM recognition_history WHERE agent IS NOT NULL GROUP BY agent;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [:] }
        defer { sqlite3_finalize(stmt) }
        var counts: [String: Int] = [:]
        while sqlite3_step(stmt) == SQLITE_ROW {
            let agent = column(stmt, 0)
            let count = Int(sqlite3_column_int(stmt, 1))
            counts[agent] = count
        }
        return counts
    }

    /// Fetch recent records with non-empty rawText for smart correction UI.
    func recentForCorrection(limit: Int = 20) -> [(id: String, date: Date, rawText: String)] {
        let sql = """
        SELECT id, created_at, raw_text FROM recognition_history
        WHERE raw_text != '' AND status = 'completed'
        ORDER BY created_at DESC LIMIT \(limit);
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }

        let iso = ISO8601DateFormatter()
        var results: [(id: String, date: Date, rawText: String)] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            results.append((
                id: column(stmt, 0),
                date: iso.date(from: column(stmt, 1)) ?? Date(),
                rawText: column(stmt, 2)
            ))
        }
        return results
    }

    func count(from start: Date? = nil, to end: Date? = nil) -> Int {
        var sql = "SELECT COUNT(*) FROM recognition_history"
        let iso = ISO8601DateFormatter()
        if let start, let end {
            sql += " WHERE created_at >= '\(iso.string(from: start))' AND created_at < '\(iso.string(from: end))'"
        }
        sql += ";"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return 0 }
        defer { sqlite3_finalize(stmt) }
        return sqlite3_step(stmt) == SQLITE_ROW ? Int(sqlite3_column_int(stmt, 0)) : 0
    }

    func delete(id: String) {
        let sql = "DELETE FROM recognition_history WHERE id = ?;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        defer { sqlite3_finalize(stmt) }
        bind(stmt, 1, id)
        if sqlite3_step(stmt) == SQLITE_DONE {
            postDidChangeNotification()
        }
    }

    func deleteAll() {
        if sqlite3_exec(db, "DELETE FROM recognition_history;", nil, nil, nil) == SQLITE_OK {
            postDidChangeNotification()
        }
    }

    // MARK: - Sync Support

    /// Fetch records whose created_at falls on the given date string (YYYY-MM-DD prefix match).
    func fetchForDate(_ datePrefix: String) -> [HistoryRecord] {
        let sql = "SELECT * FROM recognition_history WHERE created_at >= ? AND created_at < ? ORDER BY created_at ASC;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        bind(stmt, 1, datePrefix + "T00:00:00Z")
        // Next day
        if let date = dateFromPrefix(datePrefix),
           let next = Calendar.current.date(byAdding: .day, value: 1, to: date) {
            let fmt = DateFormatter(); fmt.dateFormat = "yyyy-MM-dd"
            bind(stmt, 2, fmt.string(from: next) + "T00:00:00Z")
        } else {
            bind(stmt, 2, datePrefix + "T99:99:99Z")
        }
        return readRows(stmt)
    }

    /// Get all distinct dates (YYYY-MM-DD) that have records, since a given date prefix.
    func distinctDates(since datePrefix: String?) -> [String] {
        let sql: String
        if let datePrefix {
            sql = "SELECT DISTINCT substr(created_at, 1, 10) AS d FROM recognition_history WHERE d >= '\(datePrefix)' ORDER BY d ASC;"
        } else {
            sql = "SELECT DISTINCT substr(created_at, 1, 10) AS d FROM recognition_history ORDER BY d ASC;"
        }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        var dates: [String] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            dates.append(column(stmt, 0))
        }
        return dates
    }

    /// Check if a record ID already exists.
    func exists(id: String) -> Bool {
        let sql = "SELECT 1 FROM recognition_history WHERE id = ? LIMIT 1;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return false }
        defer { sqlite3_finalize(stmt) }
        bind(stmt, 1, id)
        return sqlite3_step(stmt) == SQLITE_ROW
    }

    /// Import a record from another device. Skip if already exists (append-only).
    func mergeRemote(_ record: HistoryRecord) -> Bool {
        guard !exists(id: record.id) else { return false }
        insert(record)
        return true
    }

    /// Backfill device_id for old records that don't have one.
    func backfillDeviceId() {
        let sql = "UPDATE recognition_history SET device_id = ?, sync_updated_at = COALESCE(sync_updated_at, created_at) WHERE device_id IS NULL;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        defer { sqlite3_finalize(stmt) }
        bind(stmt, 1, DeviceIdentity.deviceId)
        sqlite3_step(stmt)
    }

    // MARK: - Migration

    /// 为旧记录计算并保存字数。应在应用启动时调用一次。
    func migrateCharacterCounts() async {
        let sql = """
        SELECT id, final_text FROM recognition_history
        WHERE character_count IS NULL;
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        defer { sqlite3_finalize(stmt) }

        var updates: [(id: String, count: Int)] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = column(stmt, 0)
            let text = column(stmt, 1)
            updates.append((id: id, count: text.count))
        }

        guard !updates.isEmpty else { return }

        sqlite3_exec(db, "BEGIN TRANSACTION", nil, nil, nil)
        for update in updates {
            let updateSQL = "UPDATE recognition_history SET character_count = ? WHERE id = ?;"
            var updateStmt: OpaquePointer?
            if sqlite3_prepare_v2(db, updateSQL, -1, &updateStmt, nil) == SQLITE_OK {
                sqlite3_bind_int(updateStmt, 1, Int32(update.count))
                bind(updateStmt, 2, update.id)
                sqlite3_step(updateStmt)
                sqlite3_finalize(updateStmt)
            }
        }
        sqlite3_exec(db, "COMMIT", nil, nil, nil)
        NSLog("[HistoryStore] Migrated %d records with character counts", updates.count)
    }

    // MARK: - Statistics

    struct Statistics: Sendable {
        let totalDuration: Double
        let totalCharacters: Int
        let recordCount: Int

        var averageSpeed: Double {
            guard totalDuration > 0 else { return 0 }
            return Double(totalCharacters) / totalDuration * 60
        }
    }

    func getStatistics() async -> Statistics {
        let sql = """
        SELECT
            COALESCE(SUM(CASE WHEN character_count IS NOT NULL THEN duration_seconds ELSE 0 END), 0),
            COALESCE(SUM(character_count), 0),
            COUNT(*)
        FROM recognition_history;
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            return Statistics(totalDuration: 0, totalCharacters: 0, recordCount: 0)
        }
        defer { sqlite3_finalize(stmt) }

        if sqlite3_step(stmt) == SQLITE_ROW {
            return Statistics(
                totalDuration: sqlite3_column_double(stmt, 0),
                totalCharacters: Int(sqlite3_column_int(stmt, 1)),
                recordCount: Int(sqlite3_column_int(stmt, 2))
            )
        }
        return Statistics(totalDuration: 0, totalCharacters: 0, recordCount: 0)
    }

    // MARK: - Private Helpers

    private func readRows(_ stmt: OpaquePointer?) -> [HistoryRecord] {
        let iso = ISO8601DateFormatter()
        var records: [HistoryRecord] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            records.append(HistoryRecord(
                id: column(stmt, 0),
                createdAt: iso.date(from: column(stmt, 1)) ?? Date(),
                durationSeconds: sqlite3_column_double(stmt, 2),
                rawText: column(stmt, 3),
                processingMode: optionalColumn(stmt, 4),
                processedText: optionalColumn(stmt, 5),
                finalText: column(stmt, 6),
                status: column(stmt, 7),
                characterCount: sqlite3_column_type(stmt, 8) == SQLITE_NULL ? nil : Int(sqlite3_column_int(stmt, 8)),
                asrProvider: optionalColumn(stmt, 9),
                agent: optionalColumn(stmt, 10),
                deviceId: optionalColumn(stmt, 11),
                syncUpdatedAt: optionalColumn(stmt, 12)
            ))
        }
        return records
    }

    private func dateFromPrefix(_ prefix: String) -> Date? {
        let fmt = DateFormatter(); fmt.dateFormat = "yyyy-MM-dd"
        return fmt.date(from: prefix)
    }

    private func bind(_ stmt: OpaquePointer?, _ index: Int32, _ value: String) {
        sqlite3_bind_text(stmt, index, (value as NSString).utf8String, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
    }

    private func bindOptional(_ stmt: OpaquePointer?, _ index: Int32, _ value: String?) {
        if let value { bind(stmt, index, value) } else { sqlite3_bind_null(stmt, index) }
    }

    private func column(_ stmt: OpaquePointer?, _ index: Int32) -> String {
        String(cString: sqlite3_column_text(stmt, index))
    }

    private func optionalColumn(_ stmt: OpaquePointer?, _ index: Int32) -> String? {
        sqlite3_column_text(stmt, index).map { String(cString: $0) }
    }

    private func postDidChangeNotification() {
        Task { @MainActor in
            NotificationCenter.default.post(name: .historyStoreDidChange, object: nil)
        }
    }
}
