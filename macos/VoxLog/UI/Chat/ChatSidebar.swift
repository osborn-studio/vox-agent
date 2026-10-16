import SwiftUI

struct ChatSidebar: View {

    @AppStorage("vox_show_optional_agents") private var showOptionalAgents = false
    @State private var showOtherFeishu = false
    @Environment(\.openWindow) private var openWindow
    @Environment(AppState.self) private var appState
    @Binding var selectedWorkspace: AgentWorkspace
    @Binding var selection: SidebarSelection
    let showMissionControl: Bool
    let agents: [Agent]
    let messageCounts: [String: Int]
    let statusSnapshot: AgentStatusSnapshot
    var onCollapse: () -> Void = {}
    var onAddAgent: () -> Void = {}
    var onDeleteAgent: (String) -> Void = { _ in }

    var body: some View {
        VStack(spacing: 0) {
            // Product identity
            HStack(spacing: TF.spacingSM) {
                Image(systemName: "waveform.path.ecg.rectangle")
                    .font(.system(size: 17))
                    .foregroundStyle(TF.accent)
                VStack(alignment: .leading, spacing: 0) {
                    Text("Vox Agent")
                        .font(.system(size: 14, weight: .semibold))
                    Text(L("团队 Context 记录", "Team context capture"))
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                }
                Spacer()

                Button {
                    onCollapse()
                } label: {
                    Image(systemName: "sidebar.left")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help(L("收起侧边栏", "Collapse sidebar"))
                .accessibilityLabel(L("收起侧边栏", "Collapse sidebar"))
                .accessibilityIdentifier("sidebar.collapse")

                Button {
                    // Refresh agents from workspace
                    Task {
                        let scanned = await WorkspaceScanner.shared.scanAgents()
                        NSLog("[VoxLog] Refreshed: %d agents from workspace", scanned.count)
                    }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help(L("从 ~/.osborn/ 刷新 Agent", "Refresh agents from ~/.osborn/"))
                .accessibilityLabel(L("刷新本机 Agents", "Refresh local agents"))
                .accessibilityIdentifier("sidebar.refreshAgents")
            }
            .padding(.horizontal, TF.spacingMD)
            .padding(.vertical, TF.spacingSM)
            .background(TF.surfaceRaised.opacity(0.5))

            Divider()

            if showMissionControl {
                VStack(spacing: 2) {
                    sectionLabel(L("工作区", "Workspace"))
                    workspaceRow(.missionControl, shortcut: "⌘1")
                        .keyboardShortcut("1", modifiers: [.command])
                    workspaceRow(.command, shortcut: "⌘2")
                        .keyboardShortcut("2", modifiers: [.command])
                }
                .padding(.bottom, TF.spacingXS)

                Divider()
            }

            ScrollView {
                VStack(spacing: 0) {
                    sectionLabel(L("群组", "Groups"))
                    backupGroupRow
                }.padding(.vertical, TF.spacingXS)

                DisclosureGroup(isExpanded: $showOptionalAgents) {
                    ForEach(topLevel, id: \.id) { agent in agentRow(agent) }
                } label: {
                    Text(L("可选 Agents", "Optional Agents"))
                        .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                }.padding(TF.spacingSM)

                DisclosureGroup(isExpanded: $showOtherFeishu) {
                    ForEach(appState.feishuConversations.conversations) { conversation in
                        conversationRow(conversation)
                    }
                    discoveryStatus
                    Button { appState.feishuConversations.refresh(using: appState.feishuAccount) } label: {
                        Label(L("刷新会话", "Refresh conversations"), systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(.plain)
                    .disabled(appState.feishuConversations.loading || appState.feishuAccount.conversationIdentity == nil)
                    .padding(TF.spacingSM)
                } label: {
                    Text(L("其他飞书会话", "Other Feishu conversations"))
                        .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                }.padding(TF.spacingSM)
            }

            Spacer()

            Divider()

            // Sync result toast
            if let text = appState.lastSyncSummary {
                Text(text)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .padding(.horizontal, TF.spacingMD)
                    .padding(.vertical, TF.spacingXS)
            }

            // Footer
            HStack(spacing: TF.spacingMD) {
                Button {
                    openWindow(id: "settings")
                    NSApp.activate(ignoringOtherApps: true)
                } label: {
                    Image(systemName: "gearshape")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help(L("设置", "Settings"))
                .accessibilityLabel(L("打开设置", "Open settings"))
                .accessibilityIdentifier("sidebar.settings")

                Button {
                    openWindow(id: "settings")
                    NSApp.activate(ignoringOtherApps: true)
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                        NotificationCenter.default.post(name: .navigateToVocabulary, object: nil)
                    }
                } label: {
                    Image(systemName: "book.closed")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help(L("词典管理", "Vocabulary"))
                .accessibilityLabel(L("打开词典管理", "Open vocabulary"))
                .accessibilityIdentifier("sidebar.vocabulary")

                Button {
                    appState.performSync()
                } label: {
                    if appState.isSyncing {
                        ProgressView().controlSize(.mini).frame(width: 13, height: 13)
                    } else {
                        Image(systemName: "arrow.triangle.2.circlepath")
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                    }
                }
                .buttonStyle(.plain)
                .disabled(appState.isSyncing)
                .help(L("同步（Notion 优先，未启用时使用 Git）", "Sync (Notion first, Git when disabled)"))
                .accessibilityLabel(L("同步本机记录", "Sync local records"))
                .accessibilityValue(appState.isSyncing ? L("正在同步", "Syncing") : "")
                .accessibilityHint(L("使用 Notion 或 Git 同步，不是飞书输入备份按钮。", "Syncs through Notion or Git; this is not the Feishu input backup control."))
                .accessibilityIdentifier("sidebar.sync")

                Button {
                    openWindow(id: "settings")
                    NSApp.activate(ignoringOtherApps: true)
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                        NotificationCenter.default.post(name: .navigateToAbout, object: nil)
                    }
                    Task {
                        await UpdateChecker.shared.checkNow(appState: appState)
                    }
                } label: {
                    ZStack(alignment: .topTrailing) {
                        Image(systemName: "arrow.down.circle")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(appState.hasUnseenUpdate || !appState.availableUpdates.isEmpty ? TF.accent : .secondary)
                            .frame(width: 16, height: 16)
                        if appState.hasUnseenUpdate || !appState.availableUpdates.isEmpty {
                            Circle()
                                .fill(TF.warning)
                                .frame(width: 6, height: 6)
                                .offset(x: TF.spacingXS / 2, y: -(TF.spacingXS / 2))
                        }
                    }
                }
                .buttonStyle(.plain)
                .help(L("在线更新", "Online update"))
                .accessibilityLabel(
                    appState.hasUnseenUpdate || !appState.availableUpdates.isEmpty
                        ? L("有可用更新", "Update available")
                        : L("在线更新", "Online update")
                )

                Spacer()

                Text(appState.feishuAccount.profile?.name ?? OwnerIdentity.displayName)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.tertiary)
            }
            .padding(.horizontal, TF.spacingMD)
            .padding(.vertical, TF.spacingSM)
        }
        .frame(
            minWidth: ChatLayoutMetrics.leftSidebarMinWidth,
            idealWidth: ChatLayoutMetrics.leftSidebarExpandedWidth,
            maxWidth: 360
        )
        .background(TF.surface)
    }

    // MARK: - Helpers

    private var backupGroupRow: some View {
        let selected = selectedWorkspace == .command && selection == .backupGroup
        return Button {
            selection = .backupGroup
            selectedWorkspace = .command
        } label: {
            HStack(spacing: TF.spacingSM) {
                Capsule().fill(TF.line).frame(width: 2).accessibilityHidden(true)
                Text("👥").font(.system(size: 16))
                VStack(alignment: .leading, spacing: 0) {
                    Text("Open Context Flow").font(.system(size: 14, weight: selected ? .semibold : .regular))
                    Text(L("飞书群 · 本机输入记录", "Feishu group · Local submissions"))
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .frame(minHeight: 48)
            .padding(.horizontal, TF.spacingMD)
            .background(RoundedRectangle(cornerRadius: TF.cornerSM).fill(selected ? TF.surfaceRaised : .clear))
            .overlay { RoundedRectangle(cornerRadius: TF.cornerSM).stroke(selected ? TF.accent : .clear, lineWidth: 1) }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).padding(.horizontal, TF.spacingXS)
        .accessibilityLabel("Open Context Flow")
        .accessibilityHint(L("飞书群的本机输入记录；发送后按备份设置同步。", "Local submissions for the Feishu group; sending follows your backup settings."))
        .accessibilityIdentifier("sidebar.openContextFlow")
        .accessibilityValue(selected ? L("已选择", "Selected") : "")
    }

    private var topLevel: [Agent] {
        agents.filter { $0.parentId == nil && !$0.isArchived }
    }

    private func children(of parentId: String) -> [Agent] {
        agents.filter { $0.parentId == parentId }
    }

    private var discoveryStatus: some View {
        Label(discoveryStatusText, systemImage: appState.feishuConversations.failure == nil ? "info.circle" : "exclamationmark.circle")
            .font(.caption)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(TF.spacingMD)
    }

    private var discoveryStatusText: String {
        let store = appState.feishuConversations
        switch store.failure {
        case .expired: return L("会话已过期，请重新登录。", "Session expired. Sign in again.")
        case .permission: return L("权限不足或已撤销。", "Permission missing or revoked.")
        case .unsupported: return L("此服务版本不支持会话发现。", "This server version does not support conversation discovery.")
        case .disabled: return L("完整飞书会话发现尚未启用。", "Full Feishu discovery is not enabled.")
        case .unavailable, .invalid: return L("服务不可达或响应无效，请重试。", "Service unavailable or invalid response. Retry.")
        case .signedOut: return L("请先登录飞书。", "Sign in with Feishu first.")
        case nil:
            if appState.feishuAccount.conversationIdentity == nil { return L("请先登录飞书。", "Sign in with Feishu first.") }
            if !store.forbiddenConversations.isEmpty { return L("部分群组权限已撤销。其他会话不受影响。", "Some group access was revoked. Other conversations are unaffected.") }
            if store.loading { return L("正在发现会话…", "Discovering conversations…") }
            if !store.conversations.contains(where: { $0.kind == "group" }) { return L("暂无共同群组。", "No shared groups.") }
            return L("仅会话发现，尚不支持消息。", "Discovery only. Messaging is not enabled.")
        }
    }

    private func conversationRow(_ conversation: FeishuConversation) -> some View {
        let isSelected = selectedWorkspace == .command && selection == .feishu(conversation.id)
        return Button {
            selection = .feishu(conversation.id)
            selectedWorkspace = .command
        } label: {
            HStack(spacing: TF.spacingSM) {
                Image(systemName: conversation.kind == "personal" ? "person.crop.square" : "person.2")
                VStack(alignment: .leading, spacing: TF.spacingXS) {
                    Text(conversation.name)
                        .font(.system(size: 14, weight: isSelected ? .semibold : .regular))
                    Text(conversation.kind == "personal" ? L("个人频道镜像 · 不提供回答", "Personal channel mirror · No replies") : L("仅会话发现", "Discovery only"))
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .frame(minHeight: 48)
            .padding(.horizontal, TF.spacingMD)
            .background(RoundedRectangle(cornerRadius: TF.cornerSM).fill(isSelected ? TF.surfaceRaised : .clear))
            .overlay { RoundedRectangle(cornerRadius: TF.cornerSM).stroke(isSelected ? TF.accent : .clear, lineWidth: 1) }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, TF.spacingXS)
        .accessibilityValue(isSelected ? L("已选择", "Selected") : "")
    }

    private func agentRow(_ agent: Agent, indent: Bool = false) -> some View {
        let isSelected = selectedWorkspace == .command && selection == .agent(agent.id)
        let count = messageCounts[agent.id] ?? 0
        let summary = statusSnapshot.agentSummary(for: agent.id)
        let statusDetail = agent.bindingStatus == .bound ? agentStatusDetail(summary) : L("未绑定机器人", "Bot not configured")

        return Button {
            selection = .agent(agent.id)
            selectedWorkspace = .command
        } label: {
            HStack(spacing: TF.spacingSM) {
                Capsule()
                    .fill(summary.status.color)
                    .frame(width: 2)
                    .accessibilityHidden(true)

                Text(agent.emoji)
                    .font(.system(size: indent ? 13 : 16))

                VStack(alignment: .leading, spacing: 0) {
                    Text(agent.name)
                        .font(.system(size: 14, weight: isSelected ? .semibold : .regular))
                        .foregroundStyle(.primary)

                    HStack(spacing: TF.spacingXS) {
                        Image(systemName: summary.status.symbolName)
                            .font(.system(size: 8, weight: .semibold))
                            .foregroundStyle(summary.status.color)
                            .accessibilityHidden(true)
                        Text(statusDetail)
                        if count > 0 {
                            Text("· \(L("\(count) 条消息", "\(count) msgs"))")
                        }
                    }
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                }

                Spacer()

                if agent.bindingStatus == .bound {
                    Image(systemName: "link")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.secondary)
                        .accessibilityLabel(L("已绑定", "Bound"))
                }
            }
            .frame(minHeight: 48)
            .padding(.horizontal, indent ? TF.spacingXL : TF.spacingMD)
            .background(
                RoundedRectangle(cornerRadius: TF.cornerSM)
                    .fill(isSelected ? TF.surfaceRaised : .clear)
            )
            .overlay {
                RoundedRectangle(cornerRadius: TF.cornerSM)
                    .stroke(isSelected ? TF.accent : .clear, lineWidth: 1)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, TF.spacingXS)
        .accessibilityLabel("\(agent.name), \(statusDetail)")
        .contextMenu {
            if agent.agentType == .userCreated {
                Button(L("删除 \(agent.name)", "Delete \(agent.name)"), role: .destructive) {
                    onDeleteAgent(agent.id)
                }
            }
        }
    }

    private func workspaceRow(_ workspace: AgentWorkspace, shortcut: String) -> some View {
        let isSelected = selectedWorkspace == workspace
        return Button {
            selectedWorkspace = workspace
        } label: {
            HStack(spacing: TF.spacingSM) {
                Image(systemName: workspace.symbolName)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(isSelected ? TF.accent : Color.secondary)
                    .frame(width: 16)
                Text(workspace.label)
                    .font(.system(size: 12, weight: isSelected ? .semibold : .regular))
                    .foregroundStyle(.primary)
                Spacer()
                Text(shortcut)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(.tertiary)
            }
            .padding(.horizontal, TF.spacingSM)
            .frame(height: 32)
            .background(
                RoundedRectangle(cornerRadius: TF.cornerSM)
                    .fill(isSelected ? TF.surfaceRaised : Color.clear)
            )
            .overlay(alignment: .leading) {
                if isSelected {
                    Rectangle()
                        .fill(TF.accent)
                        .frame(width: 2, height: 20)
                        .accessibilityHidden(true)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, TF.spacingXS)
        .accessibilityLabel(workspace.label)
        .accessibilityValue(isSelected ? L("已选择", "Selected") : "")
    }

    private func agentStatusDetail(_ summary: AgentStatusSnapshot.AgentSummary) -> String {
        if summary.awaitingApprovalCount > 0 {
            return L(
                "\(summary.awaitingApprovalCount) 项等待批准",
                "\(summary.awaitingApprovalCount) awaiting approval"
            )
        }
        if summary.failedCount > 0 {
            return L("\(summary.failedCount) 项失败", "\(summary.failedCount) failed")
        }
        if let title = summary.runningTaskTitle {
            return "\(TF.Status.running.label) · \(title)"
        }
        if summary.queuedCount > 0 {
            return L("\(summary.queuedCount) 项排队中", "\(summary.queuedCount) queued")
        }
        return TF.Status.idle.label
    }

    private func sectionLabel(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, TF.spacingMD)
            .padding(.top, TF.spacingSM)
            .padding(.bottom, TF.spacingXS)
    }
}
