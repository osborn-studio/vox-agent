import SwiftUI

enum AgentWorkspace: String, CaseIterable {
    case missionControl
    case command

    var label: String {
        switch self {
        case .missionControl: return L("任务总览", "Mission Control")
        case .command: return L("指挥台", "Command")
        }
    }

    var symbolName: String {
        switch self {
        case .missionControl: return "square.grid.2x2"
        case .command: return "command"
        }
    }
}

struct MissionControlModel {
    let tasks: [VoxTask]
    let approvals: [PermissionApproval]
    let now: Date

    var liveTasks: [VoxTask] {
        tasks
            .filter(\.isActive)
            .sorted { lhs, rhs in
                let lhsRank = Self.liveRank(lhs.status)
                let rhsRank = Self.liveRank(rhs.status)
                if lhsRank != rhsRank { return lhsRank < rhsRank }
                if lhs.updatedAt != rhs.updatedAt { return lhs.updatedAt > rhs.updatedAt }
                return lhs.id < rhs.id
            }
    }

    var failedTasks: [VoxTask] {
        tasks
            .filter { $0.status == .failed }
            .sorted { lhs, rhs in
                if lhs.updatedAt != rhs.updatedAt { return lhs.updatedAt > rhs.updatedAt }
                return lhs.id < rhs.id
            }
    }

    var pendingApprovals: [PermissionApproval] {
        approvals
            .filter { $0.isPending && $0.expiresAt > now }
            .sorted { lhs, rhs in
                if lhs.requestedAt != rhs.requestedAt { return lhs.requestedAt > rhs.requestedAt }
                return lhs.id < rhs.id
            }
    }

    var activeAgentIDs: [String] {
        Array(Set(liveTasks.map(\.assignedTo))).sorted()
    }

    static func status(for taskStatus: VoxTask.TaskStatus) -> TF.Status {
        switch taskStatus {
        case .running: return .running
        case .failed: return .failed
        case .completed: return .completed
        case .draft, .planned, .assigned, .blocked, .cancelled: return .idle
        }
    }

    private static func liveRank(_ status: VoxTask.TaskStatus) -> Int {
        switch status {
        case .running: return 0
        case .blocked: return 1
        case .assigned: return 2
        case .planned: return 3
        case .draft: return 4
        case .failed, .completed, .cancelled: return 5
        }
    }
}

struct MissionControlView: View {
    let tasks: [VoxTask]
    let agents: [Agent]
    let approvals: [PermissionApproval]
    let snapshot: AgentStatusSnapshot
    let leftSidebarVisible: Bool
    let inspectorVisible: Bool
    let onToggleLeftSidebar: () -> Void
    let onToggleInspector: () -> Void
    let onOpenCommand: () -> Void
    let onSelectAgent: (String) -> Void
    let onOpenApproval: (PermissionApproval) -> Void

    private var model: MissionControlModel {
        MissionControlModel(tasks: tasks, approvals: approvals, now: Date())
    }

    private var topLevelAgents: [Agent] {
        agents.filter { $0.parentId == nil && !$0.isArchived }
    }

    var body: some View {
        VStack(spacing: 0) {
            workspaceHeader
            AgentStatusStrip(snapshot: snapshot)
            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: TF.spacingXL) {
                    missionBrief
                    metricGrid
                    liveWorkSection
                    attentionSection
                    agentFleetSection
                }
                .frame(maxWidth: 920, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.horizontal, TF.spacingXL)
                .padding(.vertical, TF.spacingXL)
            }
        }
        .background(TF.appBackground)
        .frame(minWidth: ChatLayoutMetrics.conversationMinWidth, maxWidth: .infinity)
    }

    private var workspaceHeader: some View {
        HStack(spacing: TF.spacingSM) {
            sidebarToggle(
                symbol: "sidebar.left",
                active: leftSidebarVisible,
                label: leftSidebarVisible ? L("收起左侧栏", "Collapse left sidebar") : L("展开左侧栏", "Expand left sidebar"),
                action: onToggleLeftSidebar
            )

            VStack(alignment: .leading, spacing: 1) {
                Text(L("Vox Agent 任务总览", "Vox Agent Mission Control"))
                    .font(.system(size: 15, weight: .semibold))
                Text(L("真实任务、审批与 Agent 状态", "Live work, approvals, and Agent state"))
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 0)

            sidebarToggle(
                symbol: "sidebar.right",
                active: inspectorVisible,
                label: inspectorVisible ? L("收起操作台", "Collapse operations") : L("展开操作台", "Expand operations"),
                action: onToggleInspector
            )
        }
        .frame(height: ChatLayoutMetrics.conversationToolbarHeight)
        .padding(.horizontal, TF.spacingMD)
        .background(TF.surface)
    }

    private var missionBrief: some View {
        HStack(alignment: .bottom, spacing: TF.spacingXL) {
            VStack(alignment: .leading, spacing: TF.spacingSM) {
                Text("VOX AGENT / MISSION CONTROL")
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .foregroundStyle(.secondary)
                Text(L("协调结果，而不只是记录文字。", "Coordinate outcomes, not transcripts."))
                    .font(.system(size: 26, weight: .semibold))
                    .fixedSize(horizontal: false, vertical: true)
                Text(L(
                    "从这里查看每个 Agent 正在做什么、哪些动作需要你批准，以及下一项工作在哪里受阻。",
                    "See what every Agent is doing, which actions need approval, and where the next outcome is blocked."
                ))
                .font(.system(size: 13))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

                Label(
                    L("\(model.activeAgentIDs.count) 个 Agent 参与当前工作", "\(model.activeAgentIDs.count) Agents engaged in current work"),
                    systemImage: "person.2"
                )
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
            }

            Spacer(minLength: TF.spacingMD)

            Button(action: onOpenCommand) {
                Label(L("新建命令", "New command"), systemImage: "arrow.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, TF.spacingLG)
                    .frame(height: 34)
                    .background(RoundedRectangle(cornerRadius: TF.cornerSM).fill(TF.accent))
            }
            .buttonStyle(.plain)
            .keyboardShortcut("n", modifiers: [.command])
            .accessibilityHint(L("打开 Agent 指挥时间线", "Open the Agent command timeline"))
        }
        .padding(TF.spacingXL)
        .background(TF.surface)
        .clipShape(RoundedRectangle(cornerRadius: TF.cornerMD))
        .overlay(RoundedRectangle(cornerRadius: TF.cornerMD).stroke(TF.line, lineWidth: 1))
    }

    private var metricGrid: some View {
        LazyVGrid(
            columns: Array(repeating: GridItem(.flexible(), spacing: TF.spacingMD), count: 4),
            spacing: TF.spacingMD
        ) {
            MissionMetricCard(status: .running, value: snapshot.runningCount, label: L("运行中", "Running"))
            MissionMetricCard(status: .idle, value: snapshot.queuedCount, label: L("队列中", "Queued"))
            MissionMetricCard(status: .awaitingApproval, value: model.pendingApprovals.count, label: L("待批准", "Awaiting"))
            MissionMetricCard(status: .failed, value: snapshot.failedCount, label: L("失败", "Failed"))
        }
    }

    private var liveWorkSection: some View {
        MissionSection(title: L("正在推进", "LIVE WORK"), count: model.liveTasks.count) {
            if model.liveTasks.isEmpty {
                MissionEmptyRow(
                    symbol: "checkmark.circle",
                    title: L("当前没有执行中的工作", "No work is in flight"),
                    detail: L("创建一条命令，让 Vox Agent 选择并协调合适的 Agent。", "Create a command and Vox Agent will coordinate the right Agent.")
                )
            } else {
                VStack(spacing: 0) {
                    ForEach(model.liveTasks.prefix(8)) { task in
                        MissionTaskRow(task: task, agentName: agentName(task.assignedTo))
                        if task.id != model.liveTasks.prefix(8).last?.id { Divider() }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var attentionSection: some View {
        let attentionCount = model.pendingApprovals.count + model.failedTasks.count
        MissionSection(title: L("需要你处理", "NEEDS ATTENTION"), count: attentionCount) {
            if attentionCount == 0 {
                MissionEmptyRow(
                    symbol: "shield.checkered",
                    title: L("没有等待你处理的事项", "Nothing needs your attention"),
                    detail: L("权限请求和失败任务会在这里出现。", "Permission requests and failed work will appear here.")
                )
            } else {
                VStack(spacing: 0) {
                    ForEach(model.pendingApprovals.prefix(6)) { approval in
                        Button { onOpenApproval(approval) } label: {
                            MissionAttentionRow(
                                status: .awaitingApproval,
                                title: L("\(agentName(approval.agentId)) 请求使用 \(approval.tool)", "\(agentName(approval.agentId)) requests \(approval.tool)"),
                                detail: L("查看精确范围后决定", "Review exact scope before deciding"),
                                actionLabel: L("查看审批", "Review")
                            )
                        }
                        .buttonStyle(.plain)
                        .accessibilityHint(L("在指挥时间线中打开这项审批", "Open this approval in the command timeline"))
                        Divider()
                    }

                    ForEach(model.failedTasks.prefix(6)) { task in
                        MissionAttentionRow(
                            status: .failed,
                            title: task.title,
                            detail: L("\(agentName(task.assignedTo)) · 任务失败", "\(agentName(task.assignedTo)) · task failed"),
                            actionLabel: nil
                        )
                    }
                }
            }
        }
    }

    private var agentFleetSection: some View {
        MissionSection(title: L("Agent 队伍", "AGENT FLEET"), count: topLevelAgents.count) {
            if topLevelAgents.isEmpty {
                MissionEmptyRow(
                    symbol: "person.3",
                    title: L("尚未发现 Agent", "No Agents discovered"),
                    detail: L("启动本地引擎或刷新 Agent 工作区。", "Start the local engine or refresh the Agent workspace.")
                )
            } else {
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 180), spacing: TF.spacingSM)],
                    spacing: TF.spacingSM
                ) {
                    ForEach(topLevelAgents.prefix(12)) { agent in
                        let summary = snapshot.agentSummary(for: agent.id)
                        Button { onSelectAgent(agent.id) } label: {
                            MissionAgentCard(agent: agent, summary: summary)
                        }
                        .buttonStyle(.plain)
                        .accessibilityHint(L("打开此 Agent 的指挥时间线", "Open this Agent's command timeline"))
                    }
                }
                .padding(TF.spacingMD)
            }
        }
    }

    private func sidebarToggle(
        symbol: String,
        active: Bool,
        label: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(active ? .primary : .secondary)
                .frame(width: ChatLayoutMetrics.sidebarToggleButtonSize, height: ChatLayoutMetrics.sidebarToggleButtonSize)
                .background(RoundedRectangle(cornerRadius: TF.cornerSM).fill(active ? TF.surfaceRaised : Color.clear))
        }
        .buttonStyle(.plain)
        .help(label)
        .accessibilityLabel(label)
    }

    private func agentName(_ id: String) -> String {
        agents.first(where: { $0.id == id })?.name ?? id
    }
}

private struct MissionMetricCard: View {
    let status: TF.Status
    let value: Int
    let label: String

    var body: some View {
        VStack(alignment: .leading, spacing: TF.spacingSM) {
            Label(label, systemImage: status.symbolName)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
            Text("\(value)")
                .font(.system(size: 26, weight: .semibold, design: .monospaced))
                .monospacedDigit()
                .foregroundStyle(.primary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(TF.spacingMD)
        .background(TF.surface)
        .overlay(alignment: .leading) {
            Rectangle().fill(status.color).frame(width: 2).accessibilityHidden(true)
        }
        .clipShape(RoundedRectangle(cornerRadius: TF.cornerSM))
        .overlay(RoundedRectangle(cornerRadius: TF.cornerSM).stroke(TF.line, lineWidth: 1))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(value) \(label)")
    }
}

private struct MissionSection<Content: View>: View {
    let title: String
    let count: Int
    @ViewBuilder let content: Content

    init(title: String, count: Int, @ViewBuilder content: () -> Content) {
        self.title = title
        self.count = count
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(title)
                    .font(.system(size: 10, weight: .bold, design: .monospaced))
                    .foregroundStyle(.secondary)
                Spacer()
                Text("\(count)")
                    .font(.system(size: 10, design: .monospaced))
                    .monospacedDigit()
                    .foregroundStyle(.tertiary)
            }
            .padding(.horizontal, TF.spacingMD)
            .frame(height: 32)

            Divider()
            content
        }
        .background(TF.surface)
        .clipShape(RoundedRectangle(cornerRadius: TF.cornerMD))
        .overlay(RoundedRectangle(cornerRadius: TF.cornerMD).stroke(TF.line, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(title), \(count)")
    }
}

private struct MissionTaskRow: View {
    let task: VoxTask
    let agentName: String

    private var status: TF.Status { MissionControlModel.status(for: task.status) }

    var body: some View {
        HStack(spacing: TF.spacingMD) {
            Image(systemName: status.symbolName)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(status.color)
                .frame(width: 14)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text(task.title)
                    .font(.system(size: 12, weight: .medium))
                    .lineLimit(1)
                Text(agentName)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: TF.spacingSM)

            Text(taskLabel)
                .font(.system(size: 9, design: .monospaced))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, TF.spacingMD)
        .frame(minHeight: 48)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(task.title), \(agentName), \(taskLabel)")
    }

    private var taskLabel: String {
        switch task.status {
        case .running: return L("运行中", "RUNNING")
        case .blocked: return L("受阻", "BLOCKED")
        case .assigned: return L("已分配", "ASSIGNED")
        case .planned: return L("已计划", "PLANNED")
        case .draft: return L("草稿", "DRAFT")
        case .failed: return L("失败", "FAILED")
        case .completed: return L("完成", "DONE")
        case .cancelled: return L("已取消", "CANCELLED")
        }
    }
}

private struct MissionAttentionRow: View {
    let status: TF.Status
    let title: String
    let detail: String
    let actionLabel: String?

    var body: some View {
        HStack(spacing: TF.spacingMD) {
            Image(systemName: status.symbolName)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(status.color)
                .frame(width: 14)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.system(size: 12, weight: .medium))
                    .lineLimit(1)
                Text(detail)
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer(minLength: TF.spacingSM)

            if let actionLabel {
                Text(actionLabel)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(TF.accent)
            }
        }
        .padding(.horizontal, TF.spacingMD)
        .frame(minHeight: 48)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(detail)")
    }
}

private struct MissionAgentCard: View {
    let agent: Agent
    let summary: AgentStatusSnapshot.AgentSummary

    var body: some View {
        HStack(spacing: TF.spacingSM) {
            Rectangle()
                .fill(summary.status.color)
                .frame(width: 2, height: 30)
                .accessibilityHidden(true)
            Text(agent.emoji)
                .font(.system(size: 17))
            VStack(alignment: .leading, spacing: 2) {
                Text(agent.name)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Label(agentStatusLabel, systemImage: summary.status.symbolName)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: TF.spacingXS)
            Image(systemName: "chevron.right")
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(.tertiary)
        }
        .padding(.horizontal, TF.spacingSM)
        .frame(minHeight: 48)
        .background(TF.surfaceRaised.opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: TF.cornerSM))
        .overlay(RoundedRectangle(cornerRadius: TF.cornerSM).stroke(TF.line.opacity(0.72), lineWidth: 1))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(agent.name), \(agentStatusLabel)")
    }

    private var agentStatusLabel: String {
        if summary.awaitingApprovalCount > 0 {
            return L("\(summary.awaitingApprovalCount) 项待批准", "\(summary.awaitingApprovalCount) awaiting")
        }
        if summary.failedCount > 0 {
            return L("\(summary.failedCount) 项失败", "\(summary.failedCount) failed")
        }
        if let title = summary.runningTaskTitle { return "\(TF.Status.running.label) · \(title)" }
        if summary.queuedCount > 0 { return L("\(summary.queuedCount) 项排队中", "\(summary.queuedCount) queued") }
        return TF.Status.idle.label
    }
}

private struct MissionEmptyRow: View {
    let symbol: String
    let title: String
    let detail: String

    var body: some View {
        HStack(spacing: TF.spacingMD) {
            Image(systemName: symbol)
                .font(.system(size: 16))
                .foregroundStyle(.secondary)
                .frame(width: 20)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 12, weight: .medium))
                Text(detail).font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .padding(TF.spacingMD)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}
