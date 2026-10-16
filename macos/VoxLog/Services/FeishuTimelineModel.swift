import Foundation

struct FeishuReadItem: Codable, Equatable, Identifiable, Sendable {
    let messageId: String?
    let eventId: String?
    let source: String
    let text: String?
    let createdAt: String
    let updatedAt: String
    let deleted: Bool
    let updated: Bool
    let placeholder: Bool
    let authorKind: String
    let authorId: String
    let authorName: String?
    let status: String?
    var modelTranslationAllowed: Bool? = nil
    var id: String { source == "submission" ? "e:" + (eventId ?? "") : "m:" + (messageId ?? "") }
}

struct FeishuReadPage: Codable, Sendable {
    let conversationId: String
    let mode: String
    let policyRevision: String
    let items: [FeishuReadItem]
    let nextCursor: String?
    let hasMore: Bool

    func validates(_ conversation: String) -> Bool {
        func matches(_ value: String, _ pattern: String) -> Bool {
            value.range(of: pattern, options: .regularExpression) != nil
        }
        guard conversationId == conversation, ["personal", "shared_policy", "own_submissions"].contains(mode),
              matches(policyRevision, "^[a-f0-9]{64}$"), items.count <= 50,
              hasMore == (nextCursor != nil), nextCursor.map({ matches($0, "^[A-Za-z0-9_-]{32,64}$") }) ?? true,
              Set(items.map(\.id)).count == items.count else { return false }
        return items.allSatisfy { item in
            guard matches(item.createdAt, "^[0-9]{1,16}$"), matches(item.updatedAt, "^[0-9]{1,16}$"),
                  let created = Int64(item.createdAt), let updated = Int64(item.updatedAt), updated >= created,
                  item.messageId.map({ matches($0, "^om_[A-Za-z0-9_-]{1,253}$") }) ?? true,
                  item.eventId.map({ matches($0, "^[A-Za-z0-9_-]{1,128}$") }) ?? true,
                  item.authorId.utf8.count <= 256, item.authorName.map({ $0.count <= 80 }) ?? true,
                  ["vox", "feishu_user", "feishu_app", "feishu_anonymous", "feishu_unknown"].contains(item.authorKind),
                  item.text.map({ $0.utf8.count <= 20_000 && !$0.contains("\0") }) ?? true,
                  !item.deleted && !item.placeholder || item.text == nil else { return false }
            if item.source == "submission" {
                return mode == "own_submissions" && item.eventId != nil && item.authorKind == "vox" &&
                    item.text != nil && !item.deleted && !item.updated && !item.placeholder &&
                    ["pending", "sending", "sent", "unknown", "failed", "membership_lost"].contains(item.status ?? "")
            }
            return item.source == "feishu" && mode != "own_submissions" && item.messageId != nil &&
                (item.deleted || item.placeholder || item.text != nil) && item.status == nil &&
                (item.authorKind == "vox" || item.authorName == nil)
        }
    }
}

enum FeishuReadingFailure: Error { case unavailable, invalid, reset, limit, permission }

@MainActor @Observable final class FeishuTimelineModel {
    private(set) var identity: FeishuConversationIdentity?
    private(set) var conversation: String?
    private(set) var visible = false
    private(set) var items: [FeishuReadItem] = []
    private(set) var mode: String?
    private(set) var revision: String?
    private(set) var nextCursor: String?
    private(set) var loading = false
    private(set) var failure: FeishuReadingFailure?
    private(set) var limitReached = false
    private(set) var historyRestarted = false
    var onTranslationInvalidated: (() -> Void)?
    var onTranslationSourcesChanged: (() -> Void)?
    private(set) var translationGeneration: UInt64 = 0
    private var generation: UInt64 = 0
    private var owner: UUID?
    private var latestIDs: Set<String> = []
    // Tombstones contain no body and cannot be replaced by an older page.
    private var tombstones: Set<String> = []
    private var chainStarted = false
    private var pages = 0

    private func clear() {
        translationGeneration &+= 1
        onTranslationInvalidated?()
        onTranslationSourcesChanged?()
        generation &+= 1; owner = nil; loading = false
        items = []; latestIDs = []; tombstones = []; nextCursor = nil
        mode = nil; revision = nil; failure = nil; limitReached = false; historyRestarted = false; chainStarted = false; pages = 0
    }
    func invalidate() { clear(); identity = nil; conversation = nil }
    func bind(_ value: FeishuConversationIdentity?) {
        guard identity != value else { return }
        clear(); identity = value; conversation = nil
    }
    func select(_ value: String?) {
        guard conversation != value else { return }
        clear(); conversation = value
    }
    func setVisible(_ value: Bool) {
        guard value != visible else { return }
        visible = value
        if !value { clear() }
    }
    func forbidden(_ id: String) {
        if conversation == id { clear(); conversation = nil; failure = .permission }
    }
    func suppresses(event: String, message: String?, sent: Bool) -> Bool {
        // A sent original is never represented as a current upstream message.
        if sent && mode != "own_submissions" { return true }
        return items.contains { $0.eventId == event || (message != nil && $0.messageId == message) }
    }

    func refresh(using account: FeishuAccountClient, older: Bool = false) async {
        guard visible, !loading, let identity, identity == account.conversationIdentity,
              let conversation, !older || nextCursor != nil else { return }
        if older && (pages >= 20 || items.count >= 1000) { limitReached = true; return }
        let epoch = generation, ticket = UUID(), cursor = older ? nextCursor : nil
        owner = ticket; loading = true; failure = nil
        defer { if owner == ticket { owner = nil; loading = false } }
        do {
            let page = try await account.readMessages(conversation: conversation, cursor: cursor, for: identity, accept: { [weak self] in
                guard let self else { return false }
                return self.visible && self.generation == epoch && self.owner == ticket && self.conversation == conversation && self.identity == identity
            })
            guard visible, epoch == generation, owner == ticket, self.identity == identity,
                  self.conversation == conversation, !Task.isCancelled else { return }
            if revision != nil && (revision != page.policyRevision || mode != page.mode) {
                translationGeneration &+= 1; onTranslationInvalidated?(); onTranslationSourcesChanged?()
                items = []; tombstones = []; latestIDs = []; nextCursor = nil; chainStarted = false; pages = 0
                // A page from an old chain cannot become the new authorization's first page.
                if older { revision = nil; mode = nil; failure = .reset; return }
            }
            mode = page.mode; revision = page.policyRevision
            var merged = Dictionary(uniqueKeysWithValues: items.map { ($0.id, $0) })
            let incomingIDs = Set(page.items.map(\.id))
            if !older {
                if page.hasMore && !latestIDs.isEmpty && latestIDs.isDisjoint(with: incomingIDs) {
                    // More than a page arrived: restart explicitly instead of pretending
                    // the old cursor bridges messages that were never observed.
                    merged = [:]; nextCursor = nil; chainStarted = false; pages = 0
                    historyRestarted = true
                } else {
                    let floor = page.hasMore ? page.items.compactMap { Int64($0.createdAt) }.min() : nil
                    for id in latestIDs.subtracting(incomingIDs) {
                        guard let old = merged[id] else { continue }
                        // Equal timestamps may straddle pages. Only strict interior
                        // absence is authoritative; displaced rows remain older history.
                        if page.hasMore && (floor == nil || Int64(old.createdAt)! <= floor!) { continue }
                        merged.removeValue(forKey: id); tombstones.insert(id)
                    }
                }
                latestIDs = incomingIDs
            }
            for item in page.items {
                if item.deleted { tombstones.insert(item.id) }
                if tombstones.contains(item.id) && !item.deleted { continue }
                if !item.deleted, let old = merged[item.id], Int64(old.updatedAt)! > Int64(item.updatedAt)! { continue }
                merged[item.id] = item
            }
            // Explicit bounded session, never silently discard older pagination rows.
            if merged.count > 1000 || tombstones.count > 1000 || merged.values.reduce(0, { $0 + ($1.text?.utf8.count ?? 0) }) > 8_000_000 {
                clear(); limitReached = true; return
            }
            items = merged.values.sorted { (Int64($0.createdAt)!, $0.id) < (Int64($1.createdAt)!, $1.id) }
            onTranslationSourcesChanged?()
            if older { historyRestarted = false }
            if older || !chainStarted || (nextCursor == nil && page.nextCursor != nil) {
                nextCursor = page.nextCursor; chainStarted = true; pages += 1
            }
        } catch {
            guard epoch == generation, owner == ticket, visible, !Task.isCancelled else { return }
            // Failure provides no fresh authorization: discard all cached upstream bodies.
            clear()
            failure = (error as? FeishuReadingFailure) ?? .unavailable
        }
    }
}

/// Actual window visibility is supplied by the view's NSWindow observer. Timer effects
/// are injectable so lifecycle behavior is exercised without launching a GUI.
@MainActor final class FeishuTimelineLifecycle {
    typealias Schedule = (@escaping @MainActor () -> Void) -> (() -> Void)
    private let model: FeishuTimelineModel
    private let account: FeishuAccountClient
    private let schedule: Schedule
    private var cancel: (() -> Void)?
    private var task: Task<Void, Never>?
    init(model: FeishuTimelineModel, account: FeishuAccountClient, schedule: Schedule? = nil) {
        self.model = model; self.account = account
        self.schedule = schedule ?? { action in
            let timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in
                Task { @MainActor in action() }
            }
            return { timer.invalidate() }
        }
    }
    func visibilityChanged(_ visible: Bool) {
        model.setVisible(visible)
        if !visible { cancel?(); cancel = nil; task?.cancel(); task = nil; return }
        guard cancel == nil else { return }
        poll()
        cancel = schedule { [weak self] in self?.poll() }
    }
    func selectionChanged() { task?.cancel(); task = nil; poll() }
    private func poll() {
        guard model.visible, !model.loading else { return }
        task = Task { [weak self] in
            guard let self else { return }
            await model.refresh(using: account)
        }
    }
}
