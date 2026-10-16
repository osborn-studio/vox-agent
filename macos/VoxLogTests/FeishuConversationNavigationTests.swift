import XCTest
@testable import VoxLog

@MainActor
final class FeishuConversationNavigationTests: XCTestCase {
    let identity = FeishuConversationIdentity(origin: "https://fixture.test", appId: "cli_test", tenantKey: "team", userId: "alice")
    let dm = FeishuConversation(id: "fc_personal", kind: "personal", name: "Feishu CLI", capability: "discovery_only")
    let group = FeishuConversation(id: "fc_group", kind: "group", name: "Group", capability: "discovery_only")

    func testCuratedRosterPreservesBindingsAndRejectsRetiredDiscovery() {
        let local = Agent(id: "openclaw", name: "old", emoji: "x", parentId: "coding", externalSystem: "openclaw", externalAgentRef: "bound-bot", bindingStatus: .bound)
        let result = AgentRosterPolicy.curated([Agent(id: "general", name: "General", emoji: "x")], local: [local])
        XCTAssertEqual(result.map(\.name), ["osbornpa", "ostag", "hermes", "openclaw", "osbornalpha"])
        XCTAssertEqual(result[3].externalAgentRef, "bound-bot")
        XCTAssertTrue(result[3].parentId == nil)
        let decoded = try! JSONDecoder().decode([Agent].self, from: JSONEncoder().encode(result))
        XCTAssertEqual(decoded, result)
    }

    func testGroupRoutingNeverExecutesAndAgentNeverBacksUp() {
        let group = SidebarSelection.backupGroup
        XCTAssertTrue(group.usesInputBackup)
        XCTAssertTrue(group.agentID == nil)
        XCTAssertEqual(group.localHistoryID, "coding")
        var called = false
        let repo = group.executionRepository { called = true; return "/repo" }
        XCTAssertTrue(repo == nil)
        XCTAssertFalse(called)
        XCTAssertFalse(SidebarSelection.agent("hermes").usesInputBackup)
        var model = FeishuConversationNavigation()
        model.select(.agent("coding"))
        XCTAssertEqual(model.selection, .backupGroup)
        XCTAssertEqual(SidebarRoster.reconcile(.backupGroup, ids: ["hermes"]), .backupGroup)
    }

    func testInitialSelectionDoesNotStealLaterChoice() {
        var model = FeishuConversationNavigation()
        model.bind(identity)
        let ticket = model.begin()
        XCTAssertTrue(model.accept([dm, group], ticket: ticket))
        XCTAssertEqual(model.selection, .backupGroup)
        model.select(.agent("local"))
        XCTAssertTrue(model.accept([dm, group], ticket: model.begin()))
        XCTAssertEqual(model.selection, .agent("local"))
        model.select(.feishu(group.id))
        XCTAssertTrue(model.accept([dm, group], ticket: model.begin()))
        XCTAssertEqual(model.selection, .feishu(group.id))
    }

    func testAccountOriginAndGenerationRejectLateResults() {
        var model = FeishuConversationNavigation()
        model.bind(identity)
        let old = model.begin()
        model.bind(nil)
        XCTAssertFalse(model.accept([dm], ticket: old))
        XCTAssertTrue(model.conversations.isEmpty)
        model.bind(identity)
        let previous = model.begin()
        let current = model.begin()
        XCTAssertFalse(model.accept([dm], ticket: previous))
        XCTAssertTrue(model.accept([dm], ticket: current))
        let changed = FeishuConversationIdentity(origin: "https://other.test", appId: identity.appId, tenantKey: identity.tenantKey, userId: identity.userId)
        model.bind(changed)
        XCTAssertFalse(model.accept([dm], ticket: current))
        XCTAssertTrue(model.conversations.isEmpty)
    }

    func testSelectionChangeRejectsLateLoadAnd403OnlyRemovesOne() {
        var model = FeishuConversationNavigation()
        model.bind(identity)
        XCTAssertTrue(model.accept([dm, group], ticket: model.begin()))
        let old = model.begin()
        model.select(.agent("approval-agent"))
        XCTAssertFalse(model.accept([dm], ticket: old))
        model.revoke(group.id)
        XCTAssertEqual(model.conversations, [dm])
        XCTAssertEqual(model.selection, .agent("approval-agent"))
    }

    func testRosterMergesAndPreservesFeishuAndOffline() {
        struct Row: Equatable { let id: String; let name: String }
        let local = [Row(id: "a", name: "local"), Row(id: "b", name: "custom")]
        let merged = SidebarRoster.merge(local: local, engine: [Row(id: "a", name: "engine")], id: \.id)
        XCTAssertEqual(merged, [Row(id: "a", name: "engine"), local[1]])
        XCTAssertEqual(SidebarRoster.merge(local: merged, engine: [], id: \.id), merged)
        XCTAssertEqual(SidebarRoster.reconcile(.feishu("fc_group"), ids: ["a", "b"]), .feishu("fc_group"))
        XCTAssertEqual(SidebarRoster.reconcile(.agent("missing"), ids: ["a", "b"]), .backupGroup)
    }
    func testExplicitApprovalBeforeDiscoveryAndEveryIdentityScope() {
        var model = FeishuConversationNavigation()
        model.select(.agent("approval-agent"))
        model.bind(identity)
        XCTAssertTrue(model.accept([dm, group], ticket: model.begin()))
        XCTAssertEqual(model.selection, .agent("approval-agent"))
        for changed in [
            FeishuConversationIdentity(origin: identity.origin, appId: "other-app", tenantKey: identity.tenantKey, userId: identity.userId),
            FeishuConversationIdentity(origin: identity.origin, appId: identity.appId, tenantKey: "other-tenant", userId: identity.userId),
            FeishuConversationIdentity(origin: identity.origin, appId: identity.appId, tenantKey: identity.tenantKey, userId: "other-user")
        ] {
            let previous = model.begin()
            model.bind(changed)
            XCTAssertFalse(model.accept([dm, group], ticket: previous))
            XCTAssertTrue(model.conversations.isEmpty)
        }
        XCTAssertTrue(model.accept([dm, group], ticket: model.begin()))
        model.select(.feishu(group.id))
        model.revoke(group.id)
        XCTAssertEqual(model.conversations, [dm])
        XCTAssertEqual(model.selection, .backupGroup)
    }

    func testStoreCancelsAndRejectsLateAccountResponse() async {
        let fake = DeferredDiscovery(identity)
        let store = FeishuConversationStore()
        store.refresh(using: fake)
        await Task.yield()
        await waitUntil { fake.pending != nil }
        store.invalidate(.expired)
        fake.pending?.resume(returning: FeishuDiscoveryResponse(identity: .init(appId: identity.appId, tenantKey: identity.tenantKey, userId: identity.userId), conversations: [dm]))
        fake.pending = nil
        for _ in 0..<10 { await Task.yield() }
        XCTAssertTrue(store.conversations.isEmpty)
        XCTAssertEqual(store.selection, .backupGroup)
        XCTAssertFalse(store.loading)
    }

    func testStoreExpiredFailureClearsAccount() async {
        let fake = DeferredDiscovery(identity)
        let store = FeishuConversationStore()
        store.refresh(using: fake)
        await waitUntil { fake.pending != nil }
        fake.pending?.resume(throwing: FeishuDiscoveryFailure.expired)
        fake.pending = nil
        await waitUntil { !store.loading }
        XCTAssertTrue(store.navigation.identity == nil)
        XCTAssertTrue(store.conversations.isEmpty)
    }

    func testListPermissionClearsSelectableRows() async {
        let fake = DeferredDiscovery(identity)
        let store = FeishuConversationStore()
        store.refresh(using: fake)
        await waitUntil { fake.pending != nil }
        fake.pending?.resume(returning: FeishuDiscoveryResponse(identity: .init(appId: identity.appId, tenantKey: identity.tenantKey, userId: identity.userId), conversations: [dm, group]))
        fake.pending = nil
        await waitUntil { !store.loading }
        store.refresh(using: fake)
        await waitUntil { fake.pending != nil }
        fake.pending?.resume(throwing: FeishuDiscoveryFailure.permission)
        fake.pending = nil
        await waitUntil { !store.loading }
        XCTAssertTrue(store.conversations.isEmpty)
        XCTAssertTrue(store.navigation.identity == identity)
        XCTAssertTrue(store.failure == .permission)
    }

    func testScansAndLocalAddDeleteKeepCustomEntries() {
        struct Row: Equatable { let id: String; let name: String }
        let scanned = [Row(id: "scan", name: "workspace")]
        var stored = [Row(id: "custom", name: "User created")]
        func merge(_ stored: [Row]) -> [Row] {
            let local = SidebarRoster.merge(local: stored, engine: scanned, id: \.id)
            return SidebarRoster.merge(local: local, engine: [Row(id: "scan", name: "engine")], id: \.id)
        }
        XCTAssertEqual(merge(stored), [stored[0], Row(id: "scan", name: "engine")])
        stored.append(Row(id: "new", name: "new"))
        XCTAssertTrue(merge(stored).contains(stored[1]))
        stored.removeAll { $0.id == "custom" }
        XCTAssertFalse(merge(stored).contains { $0.id == "custom" })
    }

    private func waitUntil(_ predicate: () -> Bool) async {
        let deadline = ContinuousClock.now + .seconds(2)
        while !predicate() {
            precondition(ContinuousClock.now < deadline, "discovery test timed out")
            try! await Task.sleep(for: .milliseconds(1))
        }
    }

    func testSelectionDuringLoadRestartsCurrentTicketWithoutStealingChoice() async {
        let fake = DeferredDiscovery(identity)
        let store = FeishuConversationStore()
        store.refresh(using: fake)
        await waitUntil { fake.pending != nil }
        store.select(.agent("approval-agent"))
        store.select(.agent("latest-choice"))
        store.refresh(using: fake)
        // Model the real server's single-flight scan: no new request until the
        // first response releases its slot, even if cancellation was requested.
        try! await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(fake.calls, 1)
        fake.pending?.resume(returning: FeishuDiscoveryResponse(identity: .init(appId: identity.appId, tenantKey: identity.tenantKey, userId: identity.userId), conversations: [dm]))
        fake.pending = nil
        await waitUntil { fake.pending != nil }
        fake.pending?.resume(returning: FeishuDiscoveryResponse(identity: .init(appId: identity.appId, tenantKey: identity.tenantKey, userId: identity.userId), conversations: [dm, group]))
        fake.pending = nil
        await waitUntil { !store.loading }
        XCTAssertEqual(store.conversations, [dm, group])
        XCTAssertEqual(store.selection, .agent("latest-choice"))
    }

    func testGroupRevocationDuringScanCoalescesWithoutOverlappingRequest() async {
        let fake = DeferredDiscovery(identity)
        let store = FeishuConversationStore()
        store.refresh(using: fake)
        await waitUntil { fake.pending != nil }
        store.conversationForbidden(group.id)
        try! await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(fake.calls, 1)
        let response = FeishuDiscoveryResponse(identity: .init(appId: identity.appId, tenantKey: identity.tenantKey, userId: identity.userId), conversations: [dm, group])
        fake.pending?.resume(returning: response)
        fake.pending = nil
        await waitUntil { fake.pending != nil }
        fake.pending?.resume(returning: response)
        fake.pending = nil
        await waitUntil { !store.loading }
        XCTAssertEqual(fake.calls, 2)
        XCTAssertEqual(store.conversations, [dm])
        XCTAssertTrue(store.failure == nil)
    }

    func testStoreGroup403PreservesUnrelatedMetadataWithoutGlobalFailure() async {
        let fake = DeferredDiscovery(identity)
        let store = FeishuConversationStore()
        store.refresh(using: fake)
        await waitUntil { fake.pending != nil }
        fake.pending?.resume(returning: FeishuDiscoveryResponse(identity: .init(appId: identity.appId, tenantKey: identity.tenantKey, userId: identity.userId), conversations: [dm, group]))
        fake.pending = nil
        await waitUntil { !store.loading }
        store.conversationForbidden(group.id)
        XCTAssertEqual(store.conversations, [dm])
        XCTAssertTrue(store.failure == nil)
    }

    func testNoOpExplicitApprovalNeverAutoSelectsDM() {
        var model = FeishuConversationNavigation()
        model.select(.agent("general"))
        model.bind(identity)
        XCTAssertTrue(model.accept([dm], ticket: model.begin()))
        XCTAssertEqual(model.selection, .agent("general"))
    }

}


@MainActor
private final class DeferredDiscovery: FeishuConversationTransport {
    var conversationIdentity: FeishuConversationIdentity?
    var pending: CheckedContinuation<FeishuDiscoveryResponse, Error>?
    var calls = 0
    init(_ identity: FeishuConversationIdentity) { conversationIdentity = identity }
    func discoverConversations(for identity: FeishuConversationIdentity) async throws -> FeishuDiscoveryResponse {
        calls += 1
        guard pending == nil else { throw FeishuDiscoveryFailure.unavailable }
        return try await withCheckedThrowingContinuation { pending = $0 }
    }
}
