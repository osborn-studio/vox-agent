import Foundation

// MARK: - Agent

struct Agent: Codable, Identifiable, Hashable {
    let id: String
    var name: String
    var emoji: String
    var agentType: AgentType
    var parentId: String?

    // Binding to external system (OpenClaw / 小龙虾)
    var externalSystem: String?
    var externalAgentRef: String?
    var bindingStatus: BindingStatus

    var isArchived: Bool

    enum AgentType: String, Codable {
        case systemDefault = "system_default"
        case userCreated = "user_created"
    }

    enum BindingStatus: String, Codable {
        case unbound
        case bound
    }

    init(
        id: String,
        name: String,
        emoji: String,
        agentType: AgentType = .userCreated,
        parentId: String? = nil,
        externalSystem: String? = nil,
        externalAgentRef: String? = nil,
        bindingStatus: BindingStatus = .unbound,
        isArchived: Bool = false
    ) {
        self.id = id
        self.name = name
        self.emoji = emoji
        self.agentType = agentType
        self.parentId = parentId
        self.externalSystem = externalSystem
        self.externalAgentRef = externalAgentRef
        self.bindingStatus = bindingStatus
        self.isArchived = isArchived
    }
}

// MARK: - Group

struct AgentGroup: Codable, Identifiable, Hashable {
    let id: String
    var title: String
    var emoji: String
    var memberAgentIds: [String]
}

// MARK: - Owner Identity

struct OwnerIdentity {
    static let selfId = "gilbert"
    static let displayName = "Gilbert"
}

// MARK: - Default Agents

extension Agent {
    static let defaults: [Agent] = [
        Agent(id: "osborn_pa", name: "osbornpa", emoji: "🌊", agentType: .systemDefault),
        Agent(id: "ostag", name: "ostag", emoji: "🏷️", agentType: .systemDefault),
        Agent(id: "hermes", name: "hermes", emoji: "🪽", agentType: .systemDefault),
        Agent(id: "openclaw", name: "openclaw", emoji: "🦞", agentType: .systemDefault),
        Agent(id: "osbornalpha", name: "osbornalpha", emoji: "α", agentType: .systemDefault),
    ]
}

/// Workspace/engine discoveries must not resurrect retired chat objects.
/// Keep local bindings authoritative over discovered presentation metadata.
enum AgentRosterPolicy {
    static func curated(_ discovered: [Agent], local: [Agent] = []) -> [Agent] {
        Agent.defaults.map { seed in
            var result = local.first(where: { $0.id == seed.id })
                ?? discovered.first(where: { $0.id == seed.id }) ?? seed
            result.name = seed.name
            result.agentType = .systemDefault
            result.parentId = nil
            result.isArchived = false
            return result
        }
    }
}
