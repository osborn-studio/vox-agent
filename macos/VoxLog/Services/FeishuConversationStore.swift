import Foundation
import Observation

@MainActor
protocol FeishuConversationTransport: AnyObject {
    var conversationIdentity: FeishuConversationIdentity? { get }
    func discoverConversations(for identity: FeishuConversationIdentity) async throws -> FeishuDiscoveryResponse
}

@MainActor @Observable
final class FeishuConversationStore {
    var onNavigationChange: (() -> Void)?
    private(set) var navigation = FeishuConversationNavigation()
    private(set) var loading = false
    private(set) var failure: FeishuDiscoveryFailure?
    private weak var transport: (any FeishuConversationTransport)?
    private(set) var forbiddenConversations: Set<String> = []
    private var request: Task<Void, Never>?
    private var requestID: UUID?
    private var pendingRefresh = false

    var selection: SidebarSelection { navigation.selection }
    var conversations: [FeishuConversation] { navigation.conversations }

    func bind(_ identity: FeishuConversationIdentity?) {
        guard navigation.identity != identity else { return }
        request?.cancel()
        request = nil
        requestID = nil
        pendingRefresh = false
        loading = false
        failure = nil
        forbiddenConversations = []
        navigation.bind(identity)
        onNavigationChange?()
    }

    func select(_ selection: SidebarSelection) {
        navigation.select(selection)
        onNavigationChange?()
        // Keep the old network request alive until its response releases the
        // server's single-flight slot. Its selection-bound ticket is stale;
        // coalesce changes into one fresh request after completion.
        if loading { pendingRefresh = true }
    }

    func invalidate(_ reason: FeishuDiscoveryFailure = .signedOut) {
        request?.cancel()
        request = nil
        requestID = nil
        pendingRefresh = false
        loading = false
        navigation.bind(nil)
        forbiddenConversations = []
        failure = reason
        onNavigationChange?()
    }

    // Future per-conversation routes can revoke one destination without signing out.
    func conversationForbidden(_ id: String) {
        forbiddenConversations.insert(id)
        navigation.revoke(id)
        onNavigationChange?()
        if loading { pendingRefresh = true }
    }

    private func completed(_ id: UUID) {
        guard requestID == id else { return }
        request = nil
        requestID = nil
        loading = false
        let restart = pendingRefresh
        pendingRefresh = false
        if restart, let transport { refresh(using: transport) }
    }

    func refresh(using account: any FeishuConversationTransport) {
        transport = account
        bind(account.conversationIdentity)
        guard let identity = navigation.identity else { return }
        if loading { pendingRefresh = true; return }
        let id = UUID()
        requestID = id
        let ticket = navigation.begin()
        loading = true
        failure = nil
        request = Task { [weak self] in
            defer { self?.completed(id) }
            do {
                let response = try await account.discoverConversations(for: identity)
                guard let self, !Task.isCancelled, self.navigation.isCurrent(ticket) else { return }
                _ = self.navigation.accept(response.conversations.filter { !self.forbiddenConversations.contains($0.id) }, ticket: ticket)
                self.onNavigationChange?()
            } catch {
                guard let self, !Task.isCancelled, self.navigation.isCurrent(ticket) else { return }
                if error is CancellationError { return }
                let failure = (error as? FeishuDiscoveryFailure) ?? .unavailable
                if case .expired = failure { self.invalidate(.expired) }
                else if case .signedOut = failure { self.invalidate(.signedOut) }
                else {
                    if case .permission = failure { _ = self.navigation.accept([], ticket: ticket) }
                    self.failure = failure
                    self.onNavigationChange?()
                }
            }
        }
    }
}
