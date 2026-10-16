import Foundation

enum SidebarSelection: Equatable, Sendable {
    case agent(String)
    case feishu(String)
    case backupGroup

    var isBackupGroup: Bool { self == .backupGroup }
    // This gate is shared by the composer and tested independently of SwiftUI.
    var usesInputBackup: Bool { isBackupGroup }
    func executionRepository(_ resolve: () throws -> String?) rethrows -> String? {
        isBackupGroup ? nil : try resolve()
    }
    var localHistoryID: String? { isBackupGroup ? "coding" : agentID }

    var agentID: String? {
        if case .agent(let id) = self { return id }
        return nil
    }
}

struct FeishuConversationIdentity: Hashable, Sendable {
    let origin: String
    let appId: String
    let tenantKey: String
    let userId: String
}

struct FeishuConversation: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let kind: String
    let name: String
    let capability: String
}

struct FeishuDiscoveryResponse: Decodable, Sendable {
    struct Identity: Decodable, Sendable {
        let appId: String
        let tenantKey: String
        let userId: String
    }
    let identity: Identity
    let conversations: [FeishuConversation]
}

enum FeishuDiscoveryFailure: Error { case signedOut, expired, permission, disabled, unavailable, invalid, unsupported }

struct FeishuConversationNavigation {
    struct Ticket: Equatable {
        let identity: FeishuConversationIdentity?
        let selection: SidebarSelection
        let generation: UInt64
    }
    private(set) var identity: FeishuConversationIdentity?
    private(set) var conversations: [FeishuConversation] = []
    private(set) var selection: SidebarSelection = .backupGroup
    private var generation: UInt64 = 0

    mutating func bind(_ value: FeishuConversationIdentity?) {
        guard value != identity else { return }
        identity = value
        conversations = []
        generation &+= 1
        if case .feishu = selection { selection = .backupGroup }
        // An explicit agent/approval choice is never displaced by a later login.
    }

    mutating func select(_ value: SidebarSelection) {
        selection = value == .agent("coding") ? .backupGroup : value
        generation &+= 1
    }

    mutating func begin() -> Ticket {
        generation &+= 1
        return Ticket(identity: identity, selection: selection, generation: generation)
    }

    func isCurrent(_ ticket: Ticket) -> Bool {
        ticket.identity == identity && ticket.selection == selection && ticket.generation == generation
    }

    mutating func accept(_ values: [FeishuConversation], ticket: Ticket) -> Bool {
        guard identity != nil, isCurrent(ticket) else { return false }
        conversations = values
        if case .feishu(let id) = selection, !values.contains(where: { $0.id == id }) {
            selection = .backupGroup
        }
        return true
    }

    mutating func revoke(_ id: String) {
        conversations.removeAll { $0.id == id }
        generation &+= 1
        if selection == .feishu(id) { selection = .backupGroup }
    }
}

enum SidebarRoster {
    static func merge<T>(local: [T], engine: [T], id: KeyPath<T, String>) -> [T] {
        var result = local
        for row in engine {
            if let index = result.firstIndex(where: { $0[keyPath: id] == row[keyPath: id] }) {
                result[index] = row
            } else { result.append(row) }
        }
        return result
    }

    static func reconcile(_ selection: SidebarSelection, ids: [String]) -> SidebarSelection {
        guard case .agent(let id) = selection, !ids.contains(id) else { return selection }
        return .backupGroup
    }
}
