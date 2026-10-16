import Foundation
import Observation

struct ContextWeeklySource: Decodable, Equatable, Identifiable {
    struct Attribution: Decodable, Equatable {
        let projectId: String
        let repositoryId: String
        let source: String
    }
    let eventId: String
    let text: String
    let submittedAt: String
    let targetId: String
    let shareIntent: String
    let deliveryStatus: String
    let evidenceKind: String
    let attribution: Attribution?
    var id: String { eventId }
    var isValid: Bool {
        Self.identifier(eventId, maximum: 128) && !text.isEmpty && text.utf16.count <= 8192
        && !targetId.isEmpty && targetId.utf16.count <= 80
        && shareIntent == "backup-destination" && evidenceKind == "user-statement"
        && ["pending", "sending", "unknown", "delivered", "blocked", "rejected"].contains(deliveryStatus)
        && submittedAt.count <= 64
        && Self.timestamp(submittedAt) != nil
        && (attribution.map {
            $0.source == "manual" && Self.identifier($0.projectId, maximum: 80) && $0.repositoryId.utf16.count <= 200
            && !$0.repositoryId.contains(where: { $0.isWhitespace })
            && $0.repositoryId.range(of: #"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"#, options: .regularExpression) != nil
            && !$0.repositoryId.components(separatedBy: "/").contains(where: { $0 == "." || $0 == ".." })
        } ?? true)
    }
    static func identifier(_ value: String, maximum: Int) -> Bool {
        !value.isEmpty && value.utf8.count <= maximum
        && value.utf8.allSatisfy { (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || $0 == 45 || $0 == 95 }
    }
    static func timestamp(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
}

struct ContextWeeklyPage: Decodable {
    let version: Int
    let week: String
    let timezone: String
    let kind: String
    let requiresUserReview: Bool
    let coverage: String
    let sources: [ContextWeeklySource]
    let nextCursor: String?
    let hasMore: Bool
    let omittedRecords: Bool
    let notices: [String]

    static func decode(_ data: Data) throws -> ContextWeeklyPage {
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(json.keys) == Set(["version", "week", "timezone", "kind", "requiresUserReview", "coverage", "sources", "nextCursor", "hasMore", "omittedRecords", "notices"]),
              let sources = json["sources"] as? [[String: Any]], sources.allSatisfy({ source in
                  guard Set(source.keys) == Set(["eventId", "text", "submittedAt", "targetId", "shareIntent", "deliveryStatus", "evidenceKind", "attribution"]) else { return false }
                  if source["attribution"] is NSNull { return true }
                  guard let attribution = source["attribution"] as? [String: Any] else { return false }
                  return Set(attribution.keys) == Set(["projectId", "repositoryId", "source"])
              }) else { throw ContextWeeklyReview.Failure.invalidResponse }
        return try JSONDecoder().decode(Self.self, from: data)
    }

    func validate(week expected: String) throws {
        guard version == 1, week == expected, timezone == "Asia/Shanghai",
              kind == "source-review-draft", requiresUserReview,
              coverage == "currently-authorized-accepted-backups", sources.count <= 50,
              sources.allSatisfy({ source in
                  guard source.isValid, let date = ContextWeeklySource.timestamp(source.submittedAt) else { return false }
                  return ContextWeeklyReview.monday(containing: date) == expected
              }), Set(sources.map(\.id)).count == sources.count,
              hasMore == (nextCursor != nil),
              nextCursor.map({ ContextWeeklySource.identifier($0, maximum: 256) }) ?? true,
              !notices.isEmpty, notices.count <= 10, notices.allSatisfy({ !$0.isEmpty && $0.utf16.count <= 1024 }) else {
            throw ContextWeeklyReview.Failure.invalidResponse
        }
    }
}

/// Volatile review state only. Never writes chat history, backup queues or publications.
@MainActor @Observable final class ContextWeeklyReview {
    enum Failure: Error { case invalidResponse }
    private(set) var sources: [ContextWeeklySource] = []
    private(set) var excludedSourceIDs: Set<String> = []
    var selectedSources: [ContextWeeklySource] { sources.filter { !excludedSourceIDs.contains($0.id) } }
    private(set) var loading = false
    private(set) var failed = false
    private(set) var hasMore = false
    private(set) var omitted = false
    private(set) var pages = 0
    private(set) var week = ""
    private(set) var draft = ""
    private(set) var draftLimit = false
    static let maximumDraftScalars = 300_000
    private(set) var hasDraft = false
    private(set) var draftEpoch = 0
    enum DraftCopyStatus { case none, copied, failed, denied }
    private(set) var draftCopyStatus: DraftCopyStatus = .none
    private(set) var foregroundCleared = false
    private var sessionScope: String?
    private(set) var notices: [String] = []
    @ObservationIgnored private var readTask: Task<Void, Never>?
    private var readID: UUID?
    private var generation = 0
    private var nextCursor: String?
    private var visited: Set<String> = []
    static let maximumPages = 10

    struct ProjectCoverage: Identifiable, Equatable {
        struct ID: Hashable { let project: String?; let repository: String? }
        let id: ID
        let count: Int
    }
    var projectCoverage: [ProjectCoverage] { Self.projectCoverage(for: sources) }
    private static func projectCoverage(for sources: [ContextWeeklySource]) -> [ProjectCoverage] {
        let groups = Dictionary(grouping: sources) { source in
            ProjectCoverage.ID(project: source.attribution?.projectId, repository: source.attribution?.repositoryId)
        }
        return groups.map { ProjectCoverage(id: $0.key, count: $0.value.count) }.sorted {
            if ($0.id.project ?? "") != ($1.id.project ?? "") { return ($0.id.project ?? "") < ($1.id.project ?? "") }
            return ($0.id.repository ?? "") < ($1.id.repository ?? "")
        }
    }

    enum Coverage: Equatable { case notLoaded, loading, failed, partial, exhausted }
    var coverage: Coverage {
        if failed { return .failed }
        if loading { return .loading }
        if pages == 0 { return .notLoaded }
        if hasMore || omitted { return .partial }
        return .exhausted
    }
    var coverageDescription: String {
        switch coverage {
        case .notLoaded: return "尚未读取来源；不代表没有工作。"
        case .loading: return "正在读取来源，覆盖范围尚未确定。"
        case .failed: return "来源读取失败；不能判断周记录覆盖范围。"
        case .partial: return "仅含部分获授权来源；仍有分页或排除记录。"
        case .exhausted: return "本次获授权查询已读完；不等于完整工作周记录。"
        }
    }

    nonisolated static func monday(containing date: Date) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Shanghai")!
        let days = (calendar.component(.weekday, from: date) + 5) % 7
        let monday = calendar.date(byAdding: .day, value: -days, to: calendar.startOfDay(for: date))!
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: monday)
    }

    func reset(week: String) {
        readTask?.cancel(); readTask = nil; readID = nil
        generation += 1
        draftEpoch += 1
        draftCopyStatus = .none
        foregroundCleared = false
        self.week = week
        sources = []; excludedSourceIDs = []; draft = ""; draftLimit = false; hasDraft = false; sessionScope = nil; notices = []; nextCursor = nil; visited = []
        loading = false; failed = false; hasMore = false; omitted = false; pages = 0
    }

    func suspend() {
        let hadState = foregroundCleared || loading || !sources.isEmpty || hasDraft
        reset(week: week)
        foregroundCleared = hadState
    }

    func startLoad(fetch: @escaping (String, String?) async throws -> Data,
                   scope: @escaping () throws -> String) {
        guard readTask == nil, !loading, !hasDraft, !week.isEmpty else { return }
        let id = UUID()
        readID = id
        readTask = Task { [weak self] in
            guard let self, !Task.isCancelled, self.readID == id else { return }
            await self.load(fetch: fetch, scope: scope)
            if self.readID == id { self.readTask = nil; self.readID = nil }
        }
    }

    func load(fetch: (String, String?) async throws -> Data, scope: () throws -> String = { "synthetic" }) async {
        guard !Task.isCancelled, !loading, !hasDraft, !week.isEmpty, pages == 0 || (hasMore && pages < Self.maximumPages) else { return }
        let revision = generation
        let selectedWeek = week
        let cursor = nextCursor
        loading = true; failed = false; foregroundCleared = false
        defer { if generation == revision { loading = false } }
        do {
            let currentScope = try scope()
            guard sessionScope == nil || sessionScope == currentScope else { throw Failure.invalidResponse }
            sessionScope = currentScope
            try Task.checkCancellation()
            guard revision == generation else { return }
            let data = try await fetch(selectedWeek, cursor)
            try Task.checkCancellation()
            guard revision == generation else { return }
            guard try scope() == currentScope else { throw Failure.invalidResponse }
            guard data.count <= 4_000_000 else { throw Failure.invalidResponse }
            let page = try ContextWeeklyPage.decode(data)
            try page.validate(week: selectedWeek)
            guard page.nextCursor.map({ !visited.contains($0) && $0 != cursor }) ?? true,
                  Set(sources.map(\.id)).isDisjoint(with: page.sources.map(\.id)) else { throw Failure.invalidResponse }
            if let cursor { visited.insert(cursor) }
            sources += page.sources; pages += 1
            for notice in page.notices where !notices.contains(notice) && notices.count < 10 { notices.append(notice) }
            nextCursor = page.nextCursor; hasMore = page.hasMore; omitted = omitted || page.omittedRecords
        } catch {
            guard revision == generation else { return }
            // A revoked or unavailable source must not remain in an editable derivative.
            sources = []; excludedSourceIDs = []; draft = ""; draftLimit = false; hasDraft = false; sessionScope = nil; notices = []; nextCursor = nil; visited = []; pages = 0
            hasMore = false; omitted = false; failed = true
        }
    }

    func editDraft(_ value: String, epoch: Int) {
        guard epoch == draftEpoch, hasDraft, !loading, !failed else { return }
        guard value.unicodeScalars.prefix(Self.maximumDraftScalars + 1).count <= Self.maximumDraftScalars else { draftLimit = true; return }
        draftCopyStatus = .none
        draftLimit = false; draft = value
    }

    func copyDraft(scope: () throws -> String, write: (String) -> Bool) {
        guard hasDraft, !draft.isEmpty, !loading, !failed, let owner = sessionScope else { return }
        let epoch = draftEpoch
        let text = draft
        do {
            guard try scope() == owner, epoch == draftEpoch, hasDraft, !loading, !failed,
                  sessionScope == owner, draft == text else { throw Failure.invalidResponse }
            let copied = write(text)
            if epoch == draftEpoch, hasDraft, sessionScope == owner {
                draftCopyStatus = copied ? .copied : .failed
            }
        } catch {
            suspend()
            draftCopyStatus = .denied
        }
    }

    func discardDraft() {
        guard !loading, !failed else { return }
        draftEpoch += 1
        draftCopyStatus = .none
        draft = ""; hasDraft = false; draftLimit = false
    }

    func selectSource(_ id: String, included: Bool) {
        guard !loading, !failed, !hasDraft, sources.contains(where: { $0.id == id }) else { return }
        if included { excludedSourceIDs.remove(id) } else { excludedSourceIDs.insert(id) }
        draftLimit = false
    }

    func createDraft() {
        guard !loading, !failed, !selectedSources.isEmpty, !hasDraft else { return }
        var candidate = "本人来源核对草稿 · \(week) · Asia/Shanghai\n仅含当前已加载且获授权的备份输入；不是完整工作记录，也不证明完成或验证。\n"
        candidate += coverageDescription + "\n"
        candidate += "本人选入来源：\(selectedSources.count)/\(sources.count)；未选入来源仍保留，草稿不代表全部已加载输入。\n"
        candidate += "尚有分页：\(hasMore)；部分来源排除：\(omitted)；已加载页数：\(pages)/\(Self.maximumPages)。\n"
        var scalarCount = candidate.unicodeScalars.count
        guard scalarCount <= Self.maximumDraftScalars else { draftLimit = true; return }
        for group in Self.projectCoverage(for: selectedSources) {
            let summary = "选入输入归属（不代表成果）：\(group.id.project ?? "未归属项目") · \(group.id.repository ?? "未知仓库") · \(group.count)\n"
            let summaryCount = summary.unicodeScalars.count
            guard summaryCount <= Self.maximumDraftScalars - scalarCount else { draftLimit = true; return }
            candidate += summary
            scalarCount += summaryCount
        }
        for source in selectedSources {
            let section = "\n来源 [\(source.eventId)] · \(source.submittedAt)\n个人陈述原文：\n\(source.text)\n待本人补充：成果、验证证据、未完成事项。\n"
            let sectionCount = section.unicodeScalars.count
            guard sectionCount <= Self.maximumDraftScalars - scalarCount else { draftLimit = true; return }
            candidate += section
            scalarCount += sectionCount
        }
        draftEpoch += 1
        draftCopyStatus = .none
        draftLimit = false; draft = candidate; hasDraft = true
    }
}
