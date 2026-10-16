import SwiftUI

struct AgentsSettingsTab: View, SettingsCardHelpers {

    @State private var agents: [Agent] = []
    @State private var tasks: [VoxTask] = []
    @State private var openclawAvailable = false

    // Add agent
    @State private var newName = ""
    @State private var newEmoji = "🤖"

    // Binding
    @State private var bindingAgentId: String?
    @State private var bindWorkspace = "personal"
    @State private var bindRef = ""

    private let agentStore = AgentStore()
    private let taskStore = TaskStore()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SettingsSectionHeader(
                label: "AGENTS",
                title: L("特工管理", "Agent Management"),
                description: L("管理 Agent、查看任务、绑定小龙虾 (OpenClaw)。", "Manage agents, view tasks, bind to OpenClaw.")
            )

            // OpenClaw status
            openclawStatusCard

            Spacer().frame(height: 16)

            // Agent list
            agentListCard

            Spacer().frame(height: 16)

            Text(L("当前保留五个 Agent；分别点击“绑定”配置对应的机器人。Open Context Flow 位于聊天侧栏的群组区域。", "Five agents are retained. Use Bind to configure each bot. Open Context Flow is in the chat sidebar's Groups section."))
                .font(.caption).foregroundStyle(TF.settingsTextSecondary)

            Spacer().frame(height: 16)

            // Active tasks
            if !tasks.isEmpty {
                activeTasksCard
            }
        }
        .task {
            await loadData()
        }
    }

    // MARK: - OpenClaw Status

    private var openclawStatusCard: some View {
        settingsGroupCard(L("小龙虾 (OpenClaw)", "OpenClaw"), icon: "link") {
            HStack(spacing: 12) {
                Circle()
                    .fill(openclawAvailable ? TF.success : Color.secondary.opacity(0.3))
                    .frame(width: 8, height: 8)
                Text(openclawAvailable
                     ? L("小龙虾在线 (127.0.0.1:7860)", "OpenClaw online (127.0.0.1:7860)")
                     : L("小龙虾未启动", "OpenClaw not running"))
                    .font(.system(size: 12))
                    .foregroundStyle(openclawAvailable ? TF.settingsText : TF.settingsTextTertiary)
                Spacer()
                Button {
                    Task { openclawAvailable = await OpenClawBridge.shared.isOpenClawAvailable() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 11))
                        .foregroundStyle(TF.settingsTextTertiary)
                }
                .buttonStyle(.plain)
            }
            .padding(.vertical, 6)
        }
    }

    // MARK: - Agent List

    private var agentListCard: some View {
        settingsGroupCard(L("Agent 列表", "Agent List"), icon: "person.3") {
            ForEach(agents.filter { $0.parentId == nil && !$0.isArchived }) { agent in
                agentRow(agent)

                // Sub-agents
                ForEach(agents.filter { $0.parentId == agent.id }) { sub in
                    HStack(spacing: 8) {
                        Spacer().frame(width: 20)
                        Text(sub.emoji).font(.system(size: 13))
                        Text(sub.name)
                            .font(.system(size: 12))
                            .foregroundStyle(TF.settingsTextSecondary)
                        Spacer()
                    }
                    .padding(.vertical, 2)
                }

                if agent.id != agents.filter({ $0.parentId == nil }).last?.id {
                    SettingsDivider()
                }
            }
        }
    }

    private func agentRow(_ agent: Agent) -> some View {
        HStack(spacing: 10) {
            Text(agent.emoji).font(.system(size: 18))

            VStack(alignment: .leading, spacing: 2) {
                Text(agent.name)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(TF.settingsText)

                HStack(spacing: 6) {
                    Text(agent.agentType == .systemDefault
                         ? L("系统内置", "System")
                         : L("自定义", "Custom"))
                        .font(.system(size: 10))
                        .foregroundStyle(TF.settingsTextTertiary)

                    if agent.bindingStatus == .bound {
                        HStack(spacing: 2) {
                            Text("🔗")
                                .font(.system(size: 9))
                            Text(agent.externalAgentRef ?? "")
                                .font(.system(size: 10))
                                .foregroundStyle(TF.settingsAccentGreen)
                        }
                    }
                }
            }

            Spacer()

            // Task count for this agent
            let agentTasks = tasks.filter { $0.assignedTo == agent.id && $0.isActive }
            if !agentTasks.isEmpty {
                Text("\(agentTasks.count) tasks")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(TF.settingsAccentAmber)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(RoundedRectangle(cornerRadius: 4).fill(TF.settingsAccentAmber.opacity(0.15)))
            }

            // Bind/Unbind button
            if agent.bindingStatus == .bound {
                Button {
                    Task {
                        await OpenClawBridge.shared.unbind(voxlogAgentId: agent.id)
                        await agentStore.unbindAgent(id: agent.id)
                        await loadData()
                    }
                } label: {
                    Text(L("解绑", "Unbind"))
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(TF.settingsAccentRed)
                }
                .buttonStyle(.plain)
            } else {
                Button {
                    bindingAgentId = agent.id
                    bindRef = agent.name.lowercased().replacingOccurrences(of: " ", with: "-")
                } label: {
                    Text(L("绑定", "Bind"))
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(TF.settingsAccentBlue)
                }
                .buttonStyle(.plain)
            }

            // Delete (custom only)
            if agent.agentType == .userCreated {
                Button {
                    Task {
                        await agentStore.deleteAgent(id: agent.id)
                        await loadData()
                    }
                } label: {
                    Image(systemName: "trash")
                        .font(.system(size: 10))
                        .foregroundStyle(TF.settingsAccentRed.opacity(0.6))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.vertical, 6)
        .sheet(isPresented: Binding(
            get: { bindingAgentId == agent.id },
            set: { if !$0 { bindingAgentId = nil } }
        )) {
            bindingSheet(agentId: agent.id, agentName: agent.name)
        }
    }

    // MARK: - Binding Sheet

    private func bindingSheet(agentId: String, agentName: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L("绑定到小龙虾", "Bind to OpenClaw"))
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(TF.settingsText)

            Text(L("将 \(agentName) 绑定到 OpenClaw workspace 中的 agent，任务可自动派发执行。",
                    "Bind \(agentName) to an OpenClaw workspace agent for automatic task dispatch."))
                .font(.system(size: 11))
                .foregroundStyle(TF.settingsTextTertiary)

            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Workspace").font(.system(size: 10, weight: .semibold)).foregroundStyle(TF.settingsTextTertiary)
                    TextField("personal", text: $bindWorkspace)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12))
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("Agent Ref").font(.system(size: 10, weight: .semibold)).foregroundStyle(TF.settingsTextTertiary)
                    TextField("agent-name", text: $bindRef)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12))
                }
            }

            HStack {
                Spacer()
                Button(L("取消", "Cancel")) { bindingAgentId = nil }
                    .buttonStyle(.plain)
                    .font(.system(size: 12))
                    .foregroundStyle(TF.settingsTextSecondary)
                Button(L("绑定", "Bind")) {
                    Task {
                        await OpenClawBridge.shared.bind(
                            voxlogAgentId: agentId,
                            workspace: bindWorkspace,
                            agentRef: bindRef
                        )
                        await agentStore.bindAgent(
                            id: agentId,
                            externalSystem: "openclaw",
                            externalRef: "\(bindWorkspace)/\(bindRef)"
                        )
                        bindingAgentId = nil
                        await loadData()
                    }
                }
                .buttonStyle(.borderedProminent)
                .font(.system(size: 12))
                .disabled(bindRef.isEmpty)
            }
        }
        .padding(20)
        .frame(width: 400)
        .background(TF.settingsCardAlt)
    }

    // MARK: - Add Agent

    private var addAgentCard: some View {
        settingsGroupCard(L("添加 Agent", "Add Agent"), icon: "plus.circle") {
            HStack(spacing: 8) {
                TextField("🤖", text: $newEmoji)
                    .textFieldStyle(.plain)
                    .font(.system(size: 16))
                    .frame(width: 32)
                    .multilineTextAlignment(.center)
                    .padding(4)
                    .background(RoundedRectangle(cornerRadius: 6).fill(TF.settingsCardAlt))

                TextField(L("Agent 名称", "Agent name"), text: $newName)
                    .textFieldStyle(.plain)
                    .font(.system(size: 13))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .background(RoundedRectangle(cornerRadius: 6).fill(TF.settingsCardAlt))
                    .onSubmit { addAgent() }

                Button {
                    addAgent()
                } label: {
                    Image(systemName: "plus.circle.fill")
                        .font(.system(size: 18))
                        .foregroundStyle(TF.settingsAccentGreen)
                }
                .buttonStyle(.plain)
                .disabled(newName.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding(.vertical, 4)
        }
    }

    // MARK: - Active Tasks

    private var activeTasksCard: some View {
        settingsGroupCard(L("活跃任务", "Active Tasks"), icon: "checklist") {
            ForEach(tasks.filter { $0.isActive }) { task in
                HStack(spacing: 8) {
                    Text(task.statusEmoji).font(.system(size: 13))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(task.title)
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(TF.settingsText)
                        HStack(spacing: 4) {
                            Text("→ \(task.assignedTo)")
                                .font(.system(size: 10))
                                .foregroundStyle(TF.settingsTextTertiary)
                            Text("· \(task.priority.rawValue)")
                                .font(.system(size: 10))
                                .foregroundStyle(TF.settingsTextTertiary)
                        }
                    }
                    Spacer()
                    Button {
                        Task {
                            await taskStore.updateStatus(id: task.id, to: .completed)
                            await loadData()
                        }
                    } label: {
                        Image(systemName: "checkmark.circle")
                            .font(.system(size: 12))
                            .foregroundStyle(TF.settingsAccentGreen)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.vertical, 4)
            }
        }
    }

    // MARK: - Actions

    private func addAgent() {
        let name = newName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        Task {
            let _ = await agentStore.createAgent(name: name, emoji: newEmoji.isEmpty ? "🤖" : newEmoji)
            newName = ""
            newEmoji = "🤖"
            await loadData()
        }
    }

    private func loadData() async {
        agents = await agentStore.allAgents()
        await taskStore.ensureLoaded()
        tasks = await taskStore.allTasks()
        openclawAvailable = await OpenClawBridge.shared.isOpenClawAvailable()
    }
}
