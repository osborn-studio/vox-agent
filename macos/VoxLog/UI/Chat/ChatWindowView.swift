import SwiftUI

private enum ConversationTimelineItem: Identifiable {
    case voice(VoiceMessage)
    case agent(AgentChatMessage)
    case permission(PermissionApproval)

    var id: String {
        switch self {
        case .voice(let message): return "voice-\(message.id)"
        case .agent(let message): return "agent-\(message.id)"
        case .permission(let approval): return "permission-\(approval.id)"
        }
    }

    var timestamp: Date {
        switch self {
        case .voice(let message): return message.createdAt
        case .agent(let message): return message.timestamp
        case .permission(let approval): return approval.requestedAt
        }
    }
}

struct ChatWindowView: View {

    @Environment(AppState.self) private var appState

    private var selection: SidebarSelection { appState.feishuConversations.selection }
    private var selectedAgent: String {
        get { selection.agentID ?? "" }
        nonmutating set { appState.feishuConversations.select(.agent(newValue)) }
    }
    @State private var localAgents: [Agent] = Agent.defaults
    @State private var engineAgents: [Agent] = []
    @AppStorage(MissionControlNavigation.preferenceKey) private var showMissionControl = false
    @State private var selectedWorkspace = MissionControlNavigation.initialWorkspace()
    @State private var agents: [Agent] = Agent.defaults
    @State private var messageCounts: [String: Int] = [:]
    @State private var messages: [VoiceMessage] = []
    @State private var textInput = ""
    @State private var localDrafts: [String: String] = [:]
    @State private var draftProjects: [String: ContextProjectDraft] = [:]
    @State private var contextDraft = ContextProjectDraft()
    @State private var contextProjects: [InputBackupProject] = []
    @State private var contextProjectsScope = ""
    @State private var contextProjectsCredentialScope: String?
    @State private var selectedProjectID = ""
    @State private var projectCatalogMessage: String?
    @State private var projectCatalogLoading = false
    @State private var projectFetchID = UUID()
    @State private var repositoryInspectionID = UUID()
    @State private var repositoryInspectionMessage: String?
    @State private var repositoryInspecting = false
    @State private var retainedReview: [RetainedContextInput] = []
    @State private var retainedReviewKey = ""
    @State private var retainedPage: RetainedContextPage?
    @State private var retainedReviewError = ""
    @State private var showAddAgent = false
    @State private var newAgentName = ""
    @State private var newAgentEmoji = "🤖"
    @State private var allTasks: [VoxTask] = []
    @State private var activeTasks: [VoxTask] = []
    @State private var agentMessages: [AgentChatMessage] = []
    @State private var engineMessageTimer: Timer?
    @State private var isLeftSidebarCollapsed = false
    @State private var isInspectorCollapsed = false
    @State private var focusedApprovalID: String?
    @State private var approvalConversationAgentID: String?
    @State private var approvalFocusRevision: UInt64 = 0
    @State private var isNavigationReady = false
    @State private var lastEngineRevision: UInt64 = 0
    @State private var isReconcilingEngine = false
    @State private var backupSubmissionError: String?

    @Environment(EngineCoordinator.self) private var engine: EngineCoordinator?
    @Environment(AgentStatusStore.self) private var agentStatusStore
    @Environment(\.openWindow) private var openWindow

    private let agentStore = AgentStore()
    private let historyStore = HistoryStore()
    let taskStore: TaskStore

    private var lifecycleView: some View {
        chatLayout
        .safeAreaInset(edge: .bottom) { FeishuDraftRecoveryView(outbox: appState.feishuOutbox, account: appState.feishuAccount) }
        .frame(minWidth: windowMinWidth, minHeight: 620)
        .background(TF.appBackground)
        .task {
            await loadData()
            isNavigationReady = true
            consumePendingApprovalFocus()
            syncVoiceTarget()
            startEngineMessagePolling()
            await runEngineReconciliationLoop()
        }
        .task(id: appState.feishuAccount.conversationIdentity) {
            while !Task.isCancelled {
                await appState.feishuOutbox.flush(using: appState.feishuAccount)
                do { try await Task.sleep(for: .seconds(5)) } catch { break }
            }
        }
        .task(id: appState.feishuAccount.conversationIdentity) {
            if selectedWorkspace != .command { appState.feishuConversations.select(selection) }
            appState.feishuConversations.refresh(using: appState.feishuAccount)
        }
        .task {
            while !Task.isCancelled {
                if let configuration = appState.inputBackup.configuration {
                    await appState.inputBackup.flush(configuration: configuration)
                    await appState.inputBackup.refreshReceipts(configuration: configuration)
                }
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didResignActiveNotification)) { _ in
            retainedReview = []; retainedPage = nil; retainedReviewKey = ""; retainedReviewError = ""
        }
        .onDisappear {
            retainedReview = []; retainedPage = nil; retainedReviewKey = ""; retainedReviewError = ""
            engineMessageTimer?.invalidate()
            engineMessageTimer = nil
        }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.willCloseNotification)) { _ in
            // Sync when chat window hides to background (red X button)
            // App stays alive in menu bar, so sync completes in background
            appState.performSync()
        }
    }

    var body: some View {
        lifecycleView
        .task(id: projectCatalogScope + "|" + String(selection.isBackupGroup)) { await refreshProjectCatalog() }
        .onChange(of: textInput) { old, new in
            if selection.isBackupGroup && old.isEmpty && !new.isEmpty { beginContextDraft() }
            if new.isEmpty && !contextCaptureActive { contextDraft.reset() }
        }
        .onChange(of: appState.barPhase) {
            if textInput.isEmpty && !contextCaptureActive { contextDraft.reset() }
        }
        .onChange(of: localDraftAccountKey) {
            draftProjects.removeAll()
            retainedReview = []
            retainedReviewKey = ""
            retainedPage = nil; retainedReviewError = ""
            contextDraft.reset()
            contextProjects = []
            repositoryInspectionMessage = nil
            repositoryInspectionID = UUID()
            repositoryInspecting = false
            selectedProjectID = ""
            localDrafts.removeAll()
            textInput = ""
            backupSubmissionError = nil
        }
        .onChange(of: selection) { old, new in
            backupSubmissionError = nil
            if let id = old.localHistoryID { localDrafts[id] = textInput; draftProjects[id] = contextDraft }
            contextDraft = new.localHistoryID.flatMap { draftProjects[$0] } ?? ContextProjectDraft()
            if contextDraft.frozen { selectedProjectID = contextDraft.project?.id ?? "" }
            textInput = new.localHistoryID.flatMap { localDrafts[$0] } ?? ""
            if approvalConversationAgentID != selectedAgent {
                approvalConversationAgentID = nil
            }
            if let id = focusedApprovalID,
               engine?.eventHandler.permissionRequests.first(where: { $0.id == id })?.agentId != selectedAgent {
                focusedApprovalID = nil
            }
            syncVoiceTarget()
            Task {
                await loadMessages()
                await loadTasks()
            }
        }
        .onChange(of: showMissionControl) {
            selectedWorkspace = MissionControlNavigation.workspace(selectedWorkspace, enabled: showMissionControl)
        }
        .onChange(of: appState.pendingApprovalFocusID) {
            consumePendingApprovalFocus()
        }
        .onChange(of: selectedWorkspace) {
            if selectedWorkspace == .missionControl {
                appState.feishuConversations.select(selection)
                focusedApprovalID = nil
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .agentRosterDidChange)) { _ in
            Task { await loadAgents() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .historyStoreDidChange)) { _ in
            Task {
                await loadMessages()
                await loadCounts()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .focusPermissionApproval)) { note in
            guard let id = note.object as? String else { return }
            appState.pendingApprovalFocusID = id
            consumePendingApprovalFocus()
        }
        .sheet(isPresented: $showAddAgent) {
            addAgentSheet
        }
    }

    private func consumePendingApprovalFocus() {
        // Loading the roster must finish before routing a cold-window request.
        guard isNavigationReady else { return }
        guard let route = MissionControlNavigation.consumeApprovalFocus(
            request: &appState.pendingApprovalFocusID,
            approvals: engine?.eventHandler.pendingApprovals ?? []
        ) else { return }
        approvalConversationAgentID = route.agentID
        selectedAgent = route.agentID
        selectedWorkspace = route.workspace
        focusedApprovalID = route.approvalID
        approvalFocusRevision &+= 1
    }

    // MARK: - Layout

    @ViewBuilder
    private var chatLayout: some View {
        if isLeftSidebarCollapsed && isInspectorCollapsed {
            centerPane
        } else {
            HSplitView {
                if !isLeftSidebarCollapsed {
                    leftColumn
                }
                centerPane
                if !isInspectorCollapsed {
                    rightColumn
                }
            }
        }
    }

    private var currentAgent: Agent? {
        return agents.first(where: { $0.id == selectedAgent })
    }

    private var windowMinWidth: CGFloat {
        ChatLayoutMetrics.windowMinWidth(
            leftSidebarVisible: !isLeftSidebarCollapsed,
            inspectorVisible: !isInspectorCollapsed
        )
    }

    private func sidebarToggleButton(systemImage: String, isActive: Bool, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(isActive ? .primary : .secondary)
                .frame(
                    width: ChatLayoutMetrics.sidebarToggleButtonSize,
                    height: ChatLayoutMetrics.sidebarToggleButtonSize
                )
                .background(
                    RoundedRectangle(cornerRadius: TF.cornerSM)
                        .fill(isActive ? Color.primary.opacity(0.08) : Color.clear)
                )
        }
        .buttonStyle(.plain)
        .help(help)
    }

    private var leftColumn: some View {
        ChatSidebar(
            selectedWorkspace: $selectedWorkspace,
            selection: Binding(get: { selection }, set: { appState.feishuConversations.select($0) }),
            showMissionControl: showMissionControl,
            agents: agents,
            messageCounts: messageCounts,
            statusSnapshot: agentStatusStore.snapshot,
            onCollapse: { isLeftSidebarCollapsed = true },
            onAddAgent: { showAddAgent = true },
            onDeleteAgent: { id in
                Task {
                    await agentStore.deleteAgent(id: id)
                    await loadAgents()
                }
            }
        )
        .frame(
            minWidth: ChatLayoutMetrics.leftSidebarMinWidth,
            idealWidth: ChatLayoutMetrics.leftSidebarExpandedWidth,
            maxWidth: 360
        )
    }

    @ViewBuilder
    private var centerPane: some View {
        switch MissionControlNavigation.workspace(selectedWorkspace, enabled: showMissionControl) {
        case .missionControl:
            MissionControlView(
                tasks: allTasks,
                agents: agents,
                approvals: engine?.eventHandler.permissionRequests ?? [],
                snapshot: agentStatusStore.snapshot,
                leftSidebarVisible: !isLeftSidebarCollapsed,
                inspectorVisible: !isInspectorCollapsed,
                onToggleLeftSidebar: { isLeftSidebarCollapsed.toggle() },
                onToggleInspector: { isInspectorCollapsed.toggle() },
                onOpenCommand: {
                    focusedApprovalID = nil
                    selectedWorkspace = .command
                },
                onSelectAgent: { agentID in
                    focusedApprovalID = nil
                    selectedAgent = agentID
                    selectedWorkspace = .command
                },
                onOpenApproval: { approval in
                    appState.pendingApprovalFocusID = approval.id
                    consumePendingApprovalFocus()
                }
            )
        case .command:
            if case .feishu = selection {
                feishuDiscoveryPane
            } else {
                conversationPane
            }
        }
    }

    private var rightColumn: some View {
        Group {
            if selection.isBackupGroup {
                ScrollView {
                    VStack(alignment: .leading, spacing: TF.spacingMD) {
                        VStack(alignment: .leading, spacing: TF.spacingSM) {
                            Text("Open Context Flow").font(.headline)
                            Text(L("飞书群投递", "Feishu group delivery"))
                            Text(InputBackupConfiguration.teamTargetID).font(.caption.monospaced()).textSelection(.enabled)
                            Text(L("当前备份目标：", "Configured destination: ") + (appState.inputBackup.configuration?.targetID ?? L("未启用", "Disabled")))
                                .font(.caption).textSelection(.enabled)
                            Text(appState.inputBackup.statusText).font(.caption)
                            Text(L("仅在点击发送后投递；不会启动 Agent 或重新发送历史记录。", "Delivery occurs on Send. No agent execution or automatic replay of local history."))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        .padding(TF.spacingMD)
                        Divider()
                        contextTools
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            } else { inspectorPane }
        }
        .frame(
            minWidth: ChatLayoutMetrics.inspectorMinWidth,
            idealWidth: ChatLayoutMetrics.inspectorExpandedWidth,
            maxWidth: 420
        )
    }

    private var contextTools: some View {
        VStack(alignment: .leading, spacing: TF.spacingSM) {
            Text(L("收起侧栏会清空未保存的私人输入和周报草稿；加密保留记录不会删除。", "Closing the sidebar clears unsaved private input and weekly drafts; encrypted retained records remain."))
                .font(.caption).foregroundStyle(.secondary)
                .padding(.horizontal, TF.spacingMD)
            contextProjectPicker
            ContextPrivateInboxView(store: appState.privateDraftStore, scope: appState.inputBackup.attributionScope, request: appState.inputBackup.privateContextRequest, currentScope: appState.inputBackup.attributionScopeForDisplay)
                .id("private|" + retainedScopeKey)
            ContextWeeklyReviewView(scope: appState.inputBackup.attributionScope, fetch: appState.inputBackup.fetchWeeklySources)
                .id(retainedScopeKey)
            Button(L("核对保留原文", "Review retained originals")) {
                loadRetainedPage(offset: 0, restart: true)
            }
            .padding(.horizontal, TF.spacingMD)
            RetainedContextInputsView(inputs: retainedReviewKey == retainedScopeKey ? retainedReview : [],
                page: retainedReviewKey == retainedScopeKey ? retainedPage : nil,
                error: retainedReviewKey == retainedScopeKey ? retainedReviewError : "",
                previous: { if let page = retainedPage { loadRetainedPage(offset: max(0, page.offset - 20)) } },
                next: { if let offset = retainedPage?.nextOffset { loadRetainedPage(offset: offset) } },
                onCopyRefused: {
                    retainedReview = []; retainedPage = nil; retainedReviewKey = retainedScopeKey
                    retainedReviewError = L("复制未执行，原剪贴板未改变；身份或记录已变化，请重新核对保留原文。", "Copy refused; clipboard unchanged. Identity or record changed; review originals again.")
                },
                mayCopy: appState.inputBackup.canReview)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityIdentifier("context.sidebarTools")
        .onDisappear {
            retainedReview = []; retainedPage = nil; retainedReviewKey = ""; retainedReviewError = ""
        }
    }

    private var feishuDiscoveryPane: some View {
        VStack(alignment: .leading, spacing: TF.spacingLG) {
            HStack(spacing: TF.spacingSM) {
                sidebarToggleButton(systemImage: "sidebar.left", isActive: !isLeftSidebarCollapsed,
                                    help: L("侧边栏", "Sidebar")) { isLeftSidebarCollapsed.toggle() }
                Text(appState.feishuConversations.conversations.first(where: { selection == .feishu($0.id) })?.name ?? L("飞书会话", "Feishu conversation"))
                    .font(.headline)
                Spacer()
                sidebarToggleButton(systemImage: "sidebar.right", isActive: !isInspectorCollapsed,
                                    help: L("操作台", "Operations")) { isInspectorCollapsed.toggle() }
            }
            if case .feishu(let id) = selection {
                FeishuTimeline(model: appState.feishuAccount.timeline, account: appState.feishuAccount, conversation: id)
            }
            FeishuTextComposer(outbox: appState.feishuOutbox, account: appState.feishuAccount,
                voiceControls: { AnyView(FeishuVoiceDraftControls(appState: appState)) })

        }
        .padding(TF.spacingLG)
        .frame(maxWidth: ChatLayoutMetrics.conversationContentMaxWidth, maxHeight: .infinity, alignment: .topLeading)
        .frame(minWidth: ChatLayoutMetrics.conversationMinWidth, maxWidth: .infinity)
    }

    private var conversationPane: some View {
        let conversationID = selection.localHistoryID
        let accountKey = localDraftAccountKey
        return VStack(spacing: 0) {
            if let notification = engine?.eventHandler.latestNotification {
                NotificationToast(notification: notification) {
                    engine?.eventHandler.latestNotification = nil
                }
                .padding(.top, 4)
            }

            HStack(spacing: 8) {
                leftSidebarControl

                Text(selection.isBackupGroup ? "👥" : currentAgent?.emoji ?? "🤖")
                    .font(.system(size: 19))
                VStack(alignment: .leading, spacing: 1) {
                    Text(selection.isBackupGroup ? "Open Context Flow" : currentAgent?.name ?? selectedAgent)
                        .font(.system(size: 15, weight: .semibold))
                        .lineLimit(1)
                    Text(L("\(messages.count) 条上下文消息", "\(messages.count) context messages"))
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                .layoutPriority(1)

                Spacer(minLength: 0)

                rightSidebarControl
            }
            .frame(height: ChatLayoutMetrics.conversationToolbarHeight)
            .padding(.horizontal, 10)
            .background(TF.surface)

            if selection.isBackupGroup {
                Text(L("飞书群投递 · 这里显示本机输入与原编程大师历史，不是群成员的完整聊天记录。", "Feishu group delivery · Local submissions and former Code Master history, not the group's full timeline."))
                    .font(.caption).foregroundStyle(.secondary)
                    .padding(TF.spacingSM)
            } else {
                AgentStatusStrip(snapshot: agentStatusStore.snapshot)
            }

            Divider()

            if !selection.isBackupGroup && !activeTasks.isEmpty {
                taskBar
            }

            ScrollViewReader { proxy in
                let deliveryStates = selection.isBackupGroup ? appState.inputBackup.deliveryStates() : [:]
                ScrollView {
                    LazyVStack(spacing: ChatLayoutMetrics.timelineTurnSpacing / 2) {
                        if conversationTimelineItems.isEmpty {
                            ConversationEmptyState()
                        } else {
                            ForEach(conversationTimelineItems) { item in
                                VStack(spacing: ChatLayoutMetrics.timelineTurnSpacing / 2) {
                                    conversationTimelineTurn(item, deliveryStates: deliveryStates)
                                    Divider()
                                        .opacity(0.55)
                                }
                                .id(item.id)
                            }
                        }

                        if !selection.isBackupGroup, let engine = engine {
                            ForEach(Array(engine.eventHandler.taskProgress), id: \.key) { _, progress in
                                TaskProgressBar(progress: progress)
                                    .padding(.horizontal, 12)
                            }
                        }

                        Color.clear.frame(height: 1).id("conversation-bottom")
                    }
                    .scrollTargetLayout()
                    .frame(maxWidth: ChatLayoutMetrics.conversationContentMaxWidth)
                    .frame(maxWidth: .infinity, alignment: .center)
                }
                .padding(.horizontal, ChatLayoutMetrics.conversationContentHorizontalInset)
                .padding(.vertical, 12)
                .padding(.bottom, 42)
                .overlay(alignment: .bottom) {
                    if shouldShowScrollToBottomButton {
                        scrollToBottomButton {
                            focusedApprovalID = nil
                            proxy.scrollTo("conversation-bottom", anchor: .bottom)
                        }
                        .padding(.bottom, 12)
                    }
                }
                .onChange(of: messages.count) {
                    proxy.scrollTo(pendingApprovalScrollID.map { "permission-\($0)" } ?? "conversation-bottom",
                                   anchor: pendingApprovalScrollID == nil ? .bottom : .center)
                }
                .onChange(of: agentMessages.count) {
                    proxy.scrollTo(pendingApprovalScrollID.map { "permission-\($0)" } ?? "conversation-bottom",
                                   anchor: pendingApprovalScrollID == nil ? .bottom : .center)
                }
                .onChange(of: approvalFocusRevision) {
                    guard let pendingApprovalScrollID else { return }
                    proxy.scrollTo("permission-\(pendingApprovalScrollID)", anchor: .center)
                }
                .onAppear {
                    if let pendingApprovalScrollID {
                        proxy.scrollTo("permission-\(pendingApprovalScrollID)", anchor: .center)
                    } else {
                        proxy.scrollTo("conversation-bottom", anchor: .bottom)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            Divider()

            if let backupSubmissionError {
                Text(backupSubmissionError)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, TF.spacingMD)
                    .padding(.vertical, TF.spacingXS)
            }

            ChatInputBar(
                textInput: $textInput,
                selectedAgent: selection.localHistoryID ?? "",
                onSend: { text in
                    guard selection.localHistoryID == conversationID, localDraftAccountKey == accountKey else { return false }
                    return saveTextMessage(text)
                },
                onDraftStart: beginContextDraft
            )
            .id((selection.localHistoryID ?? "") + "|" + localDraftAccountKey)
            .background(TF.surface)
        }
        .frame(minWidth: ChatLayoutMetrics.conversationMinWidth, maxWidth: .infinity)
    }

    private var leftSidebarControl: some View {
        sidebarToggleButton(
            systemImage: "sidebar.left",
            isActive: !isLeftSidebarCollapsed,
            help: isLeftSidebarCollapsed ? L("展开左侧栏", "Expand left sidebar") : L("收起左侧栏", "Collapse left sidebar")
        ) {
            isLeftSidebarCollapsed.toggle()
        }
    }

    private var rightSidebarControl: some View {
        sidebarToggleButton(
            systemImage: "sidebar.right",
            isActive: !isInspectorCollapsed,
            help: isInspectorCollapsed ? L("展开右侧栏", "Expand right sidebar") : L("收起右侧栏", "Collapse right sidebar")
        ) {
            isInspectorCollapsed.toggle()
        }
        .keyboardShortcut("i", modifiers: [.command, .option])
    }

    private var pendingApprovalScrollID: String? {
        MissionControlNavigation.pendingFocusID(
            focusedApprovalID, approvals: engine?.eventHandler.permissionRequests ?? []
        )
    }

    private var shouldShowScrollToBottomButton: Bool {
        !conversationTimelineItems.isEmpty
    }

    private var conversationTimelineItems: [ConversationTimelineItem] {
        var items = messages.map(ConversationTimelineItem.voice)
        if !selection.isBackupGroup { items.append(contentsOf: agentMessages.filter { $0.agentId == selectedAgent }.map(ConversationTimelineItem.agent)) }
        if !selection.isBackupGroup, let engine {
            items.append(contentsOf: engine.eventHandler.permissionRequests
                .filter { $0.agentId == selectedAgent }
                .map(ConversationTimelineItem.permission))
        }
        return items.sorted { lhs, rhs in
            if lhs.timestamp == rhs.timestamp {
                return lhs.id < rhs.id
            }
            return lhs.timestamp < rhs.timestamp
        }
    }

    @ViewBuilder
    private func conversationTimelineTurn(_ item: ConversationTimelineItem, deliveryStates: [String: InputBackupDeliveryState]) -> some View {
        switch item {
        case .voice(let message):
            ChatArea(
                agent: selection.localHistoryID ?? "",
                message: message,
                agents: selection.isBackupGroup ? [] : agents,
                onForward: { message, targetAgent in
                    forwardMessage(message, to: targetAgent)
                },
                onCreateTask: { message, targetAgent in
                    createTask(from: message, assignTo: targetAgent)
                },
                onRecall: recallMessage
            )
            if selection.isBackupGroup, let state = deliveryStates[message.id] {
                HStack(spacing: TF.spacingSM) {
                    Label(state.label, systemImage: state == .delivered ? "checkmark.circle" : "clock")
                    if state != .local && state != .delivered && state != .conflict {
                        Button(L("核对状态", "Check status")) {
                            Task {
                                if let config = appState.inputBackup.configuration {
                                    await appState.inputBackup.refreshReceipts(configuration: config, eventID: message.id)
                                }
                            }
                        }
                        .buttonStyle(.borderless)
                        .disabled(appState.inputBackup.refreshing)
                        .help(appState.inputBackup.refreshing ? L("正在核对投递状态，请稍候", "Checking delivery status; please wait") : state.label)
                        .accessibilityLabel(L("核对这条输入的飞书投递状态", "Check Feishu delivery status for this input"))
                    }
                }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, TF.spacingMD)
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel(state.label)
            }
            if selection.isBackupGroup, deliveryStates[message.id] == .local {
                pendingHoldRow
            }
        case .agent(let message):
            let agentInfo = agents.first(where: { $0.id == message.agentId })
            AgentTimelineTurn(
                message: message,
                agentEmoji: agentInfo?.emoji ?? "🤖",
                agentName: agentInfo?.name ?? message.agentId
            )
        case .permission(let approval):
            if let engine {
                PermissionApprovalCard(
                    approval: approval,
                    agentName: agentDisplayName(for: approval.agentId),
                    onDecision: { approved, rememberScope in
                        await engine.eventHandler.resolveApproval(
                            id: approval.id,
                            approved: approved,
                            rememberScope: rememberScope
                        )
                        if pendingApprovalScrollID == nil { focusedApprovalID = nil }
                    }
                )
                .padding(.horizontal, TF.spacingMD)
            }
        }
    }

    /// Why a "Saved locally" input has not been sent, the last attempt, and a
    /// retry. Words and a glyph carry the meaning; no status colour as text.
    private var pendingHoldRow: some View {
        let backup = appState.inputBackup
        let reason = backup.holdReason
        let text = reason?.label ?? L("等待自动发送 · 每 30 秒重试一次", "Waiting to send · Retries every 30 seconds")
        return HStack(spacing: TF.spacingSM) {
            Label(text, systemImage: reason?.needsAction == true ? "exclamationmark.circle" : "arrow.clockwise")
                .fixedSize(horizontal: false, vertical: true)
            if let attempt = backup.lastAttemptAt {
                Text(L("上次尝试 ", "Last try ") + attempt.formatted(date: .omitted, time: .shortened))
                    .monospacedDigit()
            }
            Button(L("立即重试", "Retry now")) {
                Task {
                    if let config = backup.configuration { await backup.flush(configuration: config) }
                }
            }
            .buttonStyle(.borderless)
            .disabled(backup.isFlushing || backup.configuration == nil)
            .accessibilityLabel(L("立即重试发送本机待发送的输入", "Retry sending inputs saved on this Mac"))
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, TF.spacingMD)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(L("未发送原因：", "Not sent: ") + text)
    }

    private func agentDisplayName(for agentID: String) -> String {
        agents.first(where: { $0.id == agentID })?.name ?? agentID
    }

    private func scrollToBottomButton(action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: "arrow.down")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white)
                .frame(
                    width: ChatLayoutMetrics.scrollToBottomButtonSize,
                    height: ChatLayoutMetrics.scrollToBottomButtonSize
                )
                .background(Circle().fill(TF.accent))
                .overlay(Circle().stroke(Color.white.opacity(0.34), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .help(L("跳到底部", "Jump to bottom"))
    }

    private var inspectorPane: some View {
        OperationsInspector(
            tasks: allTasks,
            agents: agents,
            approvals: engine?.eventHandler.permissionRequests ?? [],
            engineStatus: engine?.state.rawValue ?? "stopped",
            onControl: controlTask
        )
        .frame(
            minWidth: ChatLayoutMetrics.inspectorMinWidth,
            idealWidth: ChatLayoutMetrics.inspectorExpandedWidth,
            maxWidth: 420
        )
    }

    // MARK: - Add Agent Sheet

    private var addAgentSheet: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("添加 Agent")
                .font(.system(size: 15, weight: .semibold))
            HStack(spacing: 8) {
                TextField("🤖", text: $newAgentEmoji)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 40)
                TextField("Agent 名称", text: $newAgentName)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { addAgent() }
            }
            HStack {
                Spacer()
                Button("取消") { showAddAgent = false }
                Button("添加") { addAgent() }
                    .buttonStyle(.borderedProminent)
                    .disabled(newAgentName.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 320)
    }

    private func addAgent() {
        let name = newAgentName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        Task {
            let _ = await agentStore.createAgent(name: name, emoji: newAgentEmoji.isEmpty ? "🤖" : newAgentEmoji)
            await loadAgents()
            newAgentName = ""
            newAgentEmoji = "🤖"
            showAddAgent = false
        }
    }

    // MARK: - Data Loading

    private func loadData() async {
        await loadAgents()
        await loadMessages()
        await loadCounts()
        await loadTasks()
    }

    private func syncVoiceTarget() {
        guard let targetID = selection.agentID else { return }
        let agent = currentAgent
        appState.setVoiceTarget(
            id: selectedAgent,
            name: agent?.name ?? selectedAgent
        )
        Task {
            await AppDelegate.shared?.session.setAgent(targetID)
        }
    }

    private func loadTasks() async {
        await taskStore.ensureLoaded()
        allTasks = await taskStore.allTasks()
        activeTasks = allTasks.filter { $0.assignedTo == selectedAgent && $0.isActive }
    }

    private func refreshEngineOperations() async {
        guard !isReconcilingEngine,
              let engine,
              engine.state == .running else { return }
        isReconcilingEngine = true
        defer { isReconcilingEngine = false }

        do {
            let snapshot = try await engine.fetchOperations()
            let canonicalTasks = snapshot.tasks.map(\.voxTask)
            await taskStore.reconcileEngineTasks(canonicalTasks)
            allTasks = canonicalTasks
            activeTasks = canonicalTasks.filter {
                $0.assignedTo == selectedAgent && $0.isActive
            }
            engine.eventHandler.reconcilePendingApprovals(snapshot.pendingApprovals)

            // This wire schema has no explicit archived/deleted state. Omission is
            // not a tombstone; keep previously observed agents during reconnects.
            engineAgents = SidebarRoster.merge(local: engineAgents, engine: snapshot.agents.map(\.agent), id: \.id)
            agents = AgentRosterPolicy.curated(engineAgents, local: localAgents)
            if approvalConversationAgentID != selectedAgent {
                let reconciled = SidebarRoster.reconcile(selection, ids: agents.map(\.id))
                if reconciled != selection { appState.feishuConversations.select(reconciled) }
            }
        } catch {
            DebugFileLogger.log("Agent operations reconciliation failed: \(error.localizedDescription)")
        }
    }

    private func runEngineReconciliationLoop() async {
        while !Task.isCancelled {
            await refreshEngineOperations()
            do {
                try await Task.sleep(for: .seconds(5))
            } catch {
                return
            }
        }
    }

    private func controlTask(_ task: VoxTask, action: EngineTaskAction) async throws {
        guard let engine, engine.state == .running else {
            throw TaskStoreError.engineUnavailable
        }
        if action == .rerun {
            _ = try await taskStore.rerunTask(task, enginePort: engine.port)
        } else {
            try await taskStore.controlEngineTask(
                taskId: task.id,
                action: action,
                enginePort: engine.port
            )
        }
        await refreshEngineOperations()
    }

    private func loadAgents() async {
        let scannedAgents = await WorkspaceScanner.shared.scanAgents()
        let storedAgents = await agentStore.allAgents()
        localAgents = AgentRosterPolicy.curated(scannedAgents, local: storedAgents)
        agents = AgentRosterPolicy.curated(engineAgents, local: localAgents)
        if selection == .agent("general") || selection == .agent("coding") {
            appState.feishuConversations.select(.backupGroup)
        }
    }

    private func loadCounts() async {
        messageCounts = await historyStore.agentCounts()
    }

    private func loadMessages() async {
        guard selection.localHistoryID != nil else { return }
        let requestedAgent = selection.localHistoryID!
        let records = await historyStore.fetchByAgent(requestedAgent, limit: 200).reversed()
        guard requestedAgent == selection.localHistoryID, !Task.isCancelled else { return }
        messages = records.map { record in
            VoiceMessage(
                id: record.id,
                text: record.finalText,
                rawText: record.rawText != record.finalText ? record.rawText : nil,
                agent: record.agent ?? requestedAgent,
                createdAt: record.createdAt,
                durationSeconds: record.durationSeconds,
                latencyMs: 0,
                asrProvider: record.asrProvider,
                isMe: true
            )
        }
        // Equal message counts can still change row heights and the approval position.
        if pendingApprovalScrollID != nil { approvalFocusRevision &+= 1 }
    }

    // MARK: - Task Bar

    private var taskBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                Image(systemName: "checklist")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)

                ForEach(activeTasks) { task in
                    HStack(spacing: 4) {
                        Text(task.statusEmoji)
                            .font(.system(size: 12))
                        Text(task.title)
                            .font(.system(size: 12))
                            .lineLimit(1)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(
                        RoundedRectangle(cornerRadius: TF.cornerSM)
                            .fill(TF.surfaceRaised)
                    )
                    .help(L("在右侧操作台控制此任务", "Control this task in Operations"))
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 4)
        }
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.5))
    }

    // MARK: - Task Creation

    private func createTask(from msg: VoiceMessage, assignTo agentId: String) {
        guard !selection.isBackupGroup, Agent.defaults.contains(where: { $0.id == agentId }) else { return }
        Task {
            let intent = await IntentRecognizer.shared.parseIntent(
                message: msg.text, targetAgent: agentId
            )
            let task = await taskStore.createTask(
                title: intent?.title ?? String(msg.text.prefix(80)),
                assignedTo: agentId,
                description: intent?.description ?? msg.text,
                priority: intent?.priority ?? .medium,
                sourceMessageId: msg.id,
                sourceAgent: msg.agent
            )

            // Dispatch priority: canonical Agent Engine → OpenClaw → explicit local fallback.
            if let engine = engine, engine.state == .running {
                let dispatched = await taskStore.dispatchToEngine(
                    task: task, enginePort: engine.port
                )
                if dispatched {
                    await taskStore.updateStatus(id: task.id, to: .running)
                    await refreshEngineOperations()
                }
            } else if await OpenClawBridge.shared.isBound(agentId) {
                let dispatched = await OpenClawBridge.shared.dispatch(task: task)
                if dispatched {
                    await taskStore.updateStatus(id: task.id, to: .running)
                }
            }
            await loadTasks()
            NSLog("[VoxAgent] Task created from message → %@", agentId)
        }
    }

    private func recallMessage(_ msg: VoiceMessage) {
        guard msg.canRecall else { return }
        messages.removeAll { $0.id == msg.id }
        Task {
            await historyStore.delete(id: msg.id)
            await loadMessages()
            await loadCounts()
            NSLog("[VoxLog] Message recalled: %@", msg.id)
        }
    }

    private func forwardMessage(_ msg: VoiceMessage, to targetAgent: String) {
        let record = HistoryRecord(
            id: UUID().uuidString,
            createdAt: Date(),
            durationSeconds: msg.durationSeconds,
            rawText: msg.text,
            processingMode: "forwarded",
            processedText: nil,
            finalText: msg.text,
            status: "completed",
            characterCount: msg.text.count,
            asrProvider: msg.asrProvider,
            agent: targetAgent
        )
        Task {
            await historyStore.insert(record)
            await loadCounts()
            // If we're viewing the target agent, refresh
            if selectedAgent == targetAgent {
                await loadMessages()
            }
        }
        NSLog("[VoxLog] Forwarded message to %@", targetAgent)
    }

    // MARK: - Engine Message Polling

    private func startEngineMessagePolling() {
        // Poll engine event handler for new agent messages every 200ms
        engineMessageTimer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { _ in
            guard let engine = engine else { return }
            if engine.eventHandler.revision != lastEngineRevision {
                lastEngineRevision = engine.eventHandler.revision
                Task { await refreshEngineOperations() }
            }
            let newMessages = engine.eventHandler.consumeMessages()
            if !newMessages.isEmpty {
                // Filter to current agent
                let relevant = newMessages.filter { msg in
                    msg.agentId == selectedAgent || msg.taskId.isEmpty == false
                }
                agentMessages.append(contentsOf: relevant)

                // Also reload tasks to reflect status changes
                Task { await loadTasks() }
            }
        }
    }

    private var contextCaptureActive: Bool {
        switch appState.barPhase {
        case .preparing, .recording, .processing: return true
        default: return false
        }
    }

    private func loadRetainedPage(offset: Int, restart: Bool = false) {
        let owner = retainedScopeKey
        guard restart || retainedReviewKey == owner else { retainedReview = []; retainedPage = nil; return }
        do {
            let page = try appState.inputBackup.retainedPage(offset: offset, snapshot: restart ? nil : retainedPage?.snapshot)
            guard retainedScopeKey == owner else { retainedReview = []; retainedPage = nil; return }
            retainedReview = page.items; retainedPage = page; retainedReviewKey = owner; retainedReviewError = ""
        } catch {
            retainedReview = []; retainedPage = nil; retainedReviewKey = owner
            retainedReviewError = L("保留记录已变化或身份不可用；请重新点击核对保留原文。未重发或删除记录。", "Retained records changed or identity unavailable; review originals again. Nothing resent or deleted.")
        }
    }

    private var retainedScopeKey: String {
        localDraftAccountKey + "|" + (appState.inputBackup.configuration?.endpoint.absoluteString ?? "") + "|" + (appState.inputBackup.configuration?.targetID ?? "") + "|" + String(appState.inputBackup.configurationRevision)
    }

    private var projectCatalogScope: String {
        localDraftAccountKey + "|" + (appState.inputBackup.configuration?.endpoint.absoluteString ?? "")
    }

    private var projectPreferenceKey: String { "vox_context_recent_project|" + projectCatalogScope }

    private func beginContextDraft() {
        guard selection.isBackupGroup else { return }
let scope = try? appState.inputBackup.attributionScope()
        contextDraft.begin(project: scope != nil && scope == contextProjectsCredentialScope ? authorizedContextProjects.first(where: { $0.id == selectedProjectID })?.snapshot : nil, accountScope: localDraftAccountKey, credentialScope: scope)
        selectedProjectID = contextDraft.project?.id ?? ""
    }

    private var authorizedContextProjects: [InputBackupProject] {
        contextProjectsScope == projectCatalogScope ? contextProjects : []
    }

    private var contextProjectPicker: some View {
        VStack(alignment: .leading, spacing: TF.spacingXS) {
            VStack(alignment: .leading, spacing: TF.spacingSM) {
                Picker(L("项目", "Project"), selection: Binding(get: { selectedProjectID }, set: selectContextProject)) {
                    Text(L("未指定项目", "No project selected")).tag("")
                    ForEach(authorizedContextProjects) { project in Text(project.name + " · " + project.repositoryId + " · " + project.id).tag(project.id) }
                    if let frozen = contextDraft.project, contextDraft.accountScope == localDraftAccountKey, !authorizedContextProjects.contains(where: { $0.id == frozen.id }) {
                        Text(frozen.repositoryID + L("（原项目）", " (original project)")).tag(frozen.id)
                    }
                }
                .disabled(contextDraft.frozen || contextCaptureActive || projectCatalogLoading)
                .accessibilityLabel(L("输入归属项目", "Project for this input"))
                .frame(maxWidth: .infinity)
                Button(L("刷新目录", "Refresh projects")) { Task { await refreshProjectCatalog() } }
                    .disabled(projectCatalogLoading || contextCaptureActive)
                Button(L("核对本机仓库", "Inspect local repository")) { inspectContextRepository() }
                    .disabled(contextDraft.frozen || contextCaptureActive || repositoryInspecting)
                    .accessibilityLabel(L("只读核对本机代码仓库，不更改输入归属", "Inspect local repository read-only; attribution stays unchanged"))
                if projectCatalogLoading || repositoryInspecting { ProgressView().controlSize(.small) }
            }
            if contextDraft.frozen && contextDraft.accountScope == localDraftAccountKey {
                Text(L("本次输入归属已固定：", "Attribution fixed for this input: ") + (contextDraft.project?.repositoryID ?? L("未指定", "Unspecified")))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(L("先选择项目再输入；清空草稿后可切换。目录加载前输入会固定为未指定项目。", "Select a project before typing; clear the draft to switch. Input started before loading stays unattributed."))
                .font(.caption).foregroundStyle(.secondary)
            if let projectCatalogMessage { Text(projectCatalogMessage).font(.caption).foregroundStyle(.secondary) }
            if let repositoryInspectionMessage { Text(repositoryInspectionMessage).font(.caption).foregroundStyle(.secondary) }
        }
        .padding(.horizontal, TF.spacingMD).padding(.vertical, TF.spacingXS)
    }

    private func inspectContextRepository() {
        guard selection.isBackupGroup, !contextDraft.frozen, !contextCaptureActive, !repositoryInspecting else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = L("只读核对", "Inspect read-only")
        guard panel.runModal() == .OK, let directory = panel.url else { return }
        let requestID = UUID()
        let scope = projectCatalogScope
        repositoryInspectionID = requestID
        repositoryInspecting = true
        Task { @MainActor in
            defer { if repositoryInspectionID == requestID { repositoryInspecting = false } }
            do {
                let inspection = try await ContextRepositoryResolver.inspect(directory: directory)
                guard repositoryInspectionID == requestID, projectCatalogScope == scope, selection.isBackupGroup else { return }
                let matches = authorizedContextProjects.filter { inspection.repositories.contains($0.repositoryId) }
                repositoryInspectionMessage = matches.isEmpty
                    ? L("未找到可核对的授权项目；归属保持不变。", "No matching authorized project; attribution unchanged.")
                    : L("可核对项目：", "Matching projects: ") + matches.map { $0.name + " · " + $0.repositoryId }.joined(separator: "; ") + L("。请在项目菜单中明确选择；归属未自动修改。", ". Choose explicitly in the project menu; attribution was not changed automatically.")
            } catch {
                guard repositoryInspectionID == requestID, projectCatalogScope == scope, selection.isBackupGroup else { return }
                repositoryInspectionMessage = L("无法只读解析此目录；请手动选择项目。", "Cannot inspect this directory read-only; select a project manually.")
            }
        }
    }

    private func selectContextProject(_ value: String) {
        guard value != selectedProjectID, !contextDraft.frozen, textInput.isEmpty, !contextCaptureActive, !projectCatalogLoading,
              authorizedContextProjects.contains(where: { $0.id == value }) || value.isEmpty else { return }
        selectedProjectID = value
        UserDefaults.standard.set(value, forKey: projectPreferenceKey)
    }

    @MainActor private func refreshProjectCatalog() async {
        let scope = projectCatalogScope
        let fetchID = UUID()
        projectFetchID = fetchID
        guard selection.isBackupGroup, appState.feishuAccount.profile != nil, !appState.feishuAccount.loginBlocked,
              appState.inputBackup.configuration != nil else {
            contextProjects = []
            if !contextDraft.frozen { selectedProjectID = "" }
            projectCatalogMessage = nil; projectCatalogLoading = false
            return
        }
        projectCatalogLoading = true
        defer { if projectFetchID == fetchID { projectCatalogLoading = false } }
        do {
            let credentialScope = try appState.inputBackup.attributionScope()
            let projects = try await appState.inputBackup.fetchProjects()
            guard projectFetchID == fetchID, projectCatalogScope == scope,
                  try appState.inputBackup.attributionScope() == credentialScope else { return }
            contextProjects = projects
            contextProjectsCredentialScope = credentialScope
            contextProjectsScope = scope
            if !contextDraft.frozen {
                let recent = UserDefaults.standard.string(forKey: projectPreferenceKey) ?? ""
                selectedProjectID = projects.contains(where: { $0.id == recent }) ? recent : ""
            }
            projectCatalogMessage = projects.isEmpty ? L("管理员尚未授权项目；可以先记录未指定项目的输入。", "No authorized projects yet; you can record unattributed input.") : nil
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled, projectFetchID == fetchID, projectCatalogScope == scope else { return }
            contextProjects = []
            if !contextDraft.frozen { selectedProjectID = "" }
            projectCatalogMessage = L("项目目录暂不可用；原输入保留，未选项目仍可记录。", "Project catalog unavailable; draft retained. Unattributed input can still be recorded.")
        }
    }

    private var localDraftAccountKey: String {
        let profile = appState.feishuAccount.profile
        return [appState.feishuAccount.endpoint, profile?.tenantKey ?? "", profile?.openId ?? ""].joined(separator: "|")
    }

    private func saveTextMessage(_ text: String) -> Bool {
        guard !contextDraft.frozen || contextDraft.accountScope == localDraftAccountKey else {
            backupSubmissionError = L("账号已变更，原草稿不能跨账号发送。", "Account changed; this draft cannot be sent by another account.")
            return false
        }
        beginContextDraft()
        guard !selection.isBackupGroup || contextDraft.credentialScope != nil else {
            backupSubmissionError = L("请重新登录后清空并重新开始本次输入；原草稿仍保留。", "Sign in, then clear and restart this input; original draft retained.")
            return false
        }
        guard let historyID = selection.localHistoryID else { return false }
        guard selection.isBackupGroup || Agent.defaults.contains(where: { $0.id == selectedAgent }) else { return false }
        let repository: String?
        do {
            if selection.isBackupGroup {
                guard appState.feishuAccount.profile != nil, !appState.feishuAccount.loginBlocked else {
                    backupSubmissionError = L("请先登录团队飞书账号。", "Sign in to your team Feishu account first.")
                    return false
                }
                guard let config = appState.inputBackup.configuration else {
                    backupSubmissionError = L("请在同步设置中启用备份并保存有效配置。", "Enable backup and save a valid configuration in Sync settings.")
                    return false
                }
                guard config.endpoint.absoluteString == InputBackupConfiguration.teamEndpoint,
                      (try? InputBackupConfiguration(endpoint: appState.feishuAccount.endpoint, targetID: "login").endpoint.absoluteString) == InputBackupConfiguration.teamEndpoint else {
                    backupSubmissionError = L("登录与备份服务地址必须使用团队云地址。", "Login and backup must use the team cloud address.")
                    return false
                }
                guard config.targetID == InputBackupConfiguration.teamTargetID else {
                    backupSubmissionError = L("备份目标必须为 Open Context Flow，请检查同步设置。", "Set the backup target to Open Context Flow in Sync settings.")
                    return false
                }
                repository = selection.executionRepository { nil }
            } else { repository = try selection.executionRepository { try ChatSubmissionSettings.repository() } }
        }
        catch {
            backupSubmissionError = error.localizedDescription
            return false
        }
        if repository != nil && engine?.state != .running {
            backupSubmissionError = L("引擎未连接，输入尚未发送。", "Engine disconnected; input has not been sent.")
            return false
        }
        let targetAgent = selectedAgent
        backupSubmissionError = nil

        let record = HistoryRecord(
            id: UUID().uuidString,
            createdAt: Date(),
            durationSeconds: 0,
            rawText: text,
            processingMode: nil,
            processedText: nil,
            finalText: text,
            status: "completed",
            characterCount: text.count,
            asrProvider: nil,
            agent: historyID
        )
        if selection.usesInputBackup, let configuration = appState.inputBackup.configuration {
            do {
                try appState.inputBackup.enqueue(eventID: record.id, text: text, configuration: configuration, project: selection.isBackupGroup ? contextDraft.project : nil, expectedScope: selection.isBackupGroup ? contextDraft.credentialScope : nil)
                backupSubmissionError = nil
                Task { await appState.inputBackup.flush(configuration: configuration) }
            } catch {
                backupSubmissionError = L("尚未发送：", "Not sent: ") + error.localizedDescription
                return false
            }
        }
        Task {
            await historyStore.insert(record)
            await loadMessages()
            await loadCounts()

            // Record-only submissions never enter agent routing.
            guard let repository else { return }
            if let engine = engine, engine.state == .running {
                do {
                    let result = try await engine.sendCommand(
                        text: text,
                        agentId: targetAgent,
                        workingDirectory: repository
                    )
                    if !result.message.isEmpty {
                        agentMessages.append(AgentChatMessage(
                            taskId: result.taskId ?? "",
                            agentId: targetAgent,
                            type: .text,
                            content: result.message,
                            seq: 0,
                            timestamp: Date()
                        ))
                    }
                    await refreshEngineOperations()
                } catch {
                    agentMessages.append(AgentChatMessage(
                        taskId: "",
                        agentId: targetAgent,
                        type: .error,
                        content: error.localizedDescription,
                        seq: 0,
                        timestamp: Date()
                    ))
                }
            } else {
                DebugFileLogger.log(
                    "Agent command saved locally because Engine state is \(engine?.state.rawValue ?? "nil")"
                )
            }
        }
        return true
    }
}
