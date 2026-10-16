import Foundation

extension Notification.Name {
    static let agentRosterDidChange = Notification.Name("VoxLog.agentRosterDidChange")
}

/// Persists agents and groups to JSON files in Application Support.
actor AgentStore {

    private var agents: [Agent] = Agent.defaults
    private var groups: [AgentGroup] = []
    private let agentsURL: URL
    private let groupsURL: URL

    init() {
        let appSupport = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first!.appendingPathComponent("VoxLog", isDirectory: true)
        try? FileManager.default.createDirectory(at: appSupport, withIntermediateDirectories: true)
        agentsURL = appSupport.appendingPathComponent("agents.json")
        groupsURL = appSupport.appendingPathComponent("groups.json")
        // Agents start with defaults, load() is called on first allAgents() access
    }

    // MARK: - Read


    func allAgents() -> [Agent] {
        load()
        return AgentRosterPolicy.curated(agents, local: agents)
    }
    func topLevel() -> [Agent] { agents.filter { $0.parentId == nil && !$0.isArchived } }
    func children(of parentId: String) -> [Agent] { agents.filter { $0.parentId == parentId } }
    func agent(byId id: String) -> Agent? { agents.first { $0.id == id } }
    func allGroups() -> [AgentGroup] { groups }

    // MARK: - Agent CRUD

    func createAgent(name: String, emoji: String, parentId: String? = nil) -> Agent {
        load()
        let id = name.lowercased().replacingOccurrences(of: " ", with: "-") + "-" + UUID().uuidString.prefix(4).lowercased()
        let agent = Agent(id: id, name: name, emoji: emoji, parentId: parentId)
        agents.append(agent)
        save()
        return agent
    }

    func deleteAgent(id: String) {
        load()
        agents.removeAll { $0.id == id }
        // Also remove from groups
        for i in groups.indices {
            groups[i].memberAgentIds.removeAll { $0 == id }
        }
        save()
    }

    func updateAgent(id: String, name: String? = nil, emoji: String? = nil) {
        load()
        guard let idx = agents.firstIndex(where: { $0.id == id }) else { return }
        if let name { agents[idx].name = name }
        if let emoji { agents[idx].emoji = emoji }
        save()
    }

    // MARK: - Binding

    func bindAgent(id: String, externalSystem: String, externalRef: String) {
        load()
        if !agents.contains(where: { $0.id == id }), let seed = Agent.defaults.first(where: { $0.id == id }) { agents.append(seed) }
        guard let idx = agents.firstIndex(where: { $0.id == id }) else { return }
        agents[idx].externalSystem = externalSystem
        agents[idx].externalAgentRef = externalRef
        agents[idx].bindingStatus = .bound
        save()
    }

    func unbindAgent(id: String) {
        load()
        guard let idx = agents.firstIndex(where: { $0.id == id }) else { return }
        agents[idx].externalSystem = nil
        agents[idx].externalAgentRef = nil
        agents[idx].bindingStatus = .unbound
        save()
    }

    // MARK: - Group CRUD

    func createGroup(title: String, emoji: String, members: [String]) -> AgentGroup {
        load()
        let group = AgentGroup(
            id: UUID().uuidString.lowercased(),
            title: title,
            emoji: emoji,
            memberAgentIds: members
        )
        groups.append(group)
        save()
        return group
    }

    func deleteGroup(id: String) {
        load()
        groups.removeAll { $0.id == id }
        save()
    }

    func addToGroup(groupId: String, agentId: String) {
        load()
        guard let idx = groups.firstIndex(where: { $0.id == groupId }) else { return }
        if !groups[idx].memberAgentIds.contains(agentId) {
            groups[idx].memberAgentIds.append(agentId)
            save()
        }
    }

    func removeFromGroup(groupId: String, agentId: String) {
        load()
        guard let idx = groups.firstIndex(where: { $0.id == groupId }) else { return }
        groups[idx].memberAgentIds.removeAll { $0 == agentId }
        save()
    }

    // MARK: - Persistence

    private func load() {
        // Load agents
        if let data = try? Data(contentsOf: agentsURL),
           let saved = try? JSONDecoder().decode([Agent].self, from: data) {
            agents = saved
        } else {
            agents = Agent.defaults
        }

        // Load groups
        if let data = try? Data(contentsOf: groupsURL),
           let saved = try? JSONDecoder().decode([AgentGroup].self, from: data) {
            groups = saved
        }
    }

    private func save() {
        defer {
            Task { @MainActor in NotificationCenter.default.post(name: .agentRosterDidChange, object: nil) }
        }
        if let data = try? JSONEncoder().encode(agents) {
            try? data.write(to: agentsURL, options: .atomic)
        }
        if let data = try? JSONEncoder().encode(groups) {
            try? data.write(to: groupsURL, options: .atomic)
        }
    }
}
