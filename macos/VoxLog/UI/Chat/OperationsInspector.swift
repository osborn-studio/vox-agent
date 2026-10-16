import SwiftUI

enum OperationsInspectorModel {
    struct Groups: Equatable {
        let nowRunning: [VoxTask]
        let queueAndSchedule: [VoxTask]
        let historyAndAudit: [VoxTask]
    }

    static func group(_ tasks: [VoxTask]) -> Groups {
        let recentFirst = tasks.sorted { lhs, rhs in
            if lhs.updatedAt == rhs.updatedAt {
                return lhs.id < rhs.id
            }
            return lhs.updatedAt > rhs.updatedAt
        }
        return Groups(
            nowRunning: recentFirst.filter { [.running, .blocked].contains($0.status) },
            queueAndSchedule: recentFirst.filter {
                [.draft, .planned, .assigned].contains($0.status)
            },
            historyAndAudit: recentFirst.filter {
                [.completed, .failed, .cancelled].contains($0.status)
            }
        )
    }

    static func controls(for status: VoxTask.TaskStatus) -> [EngineTaskAction] {
        switch status {
        case .running:
            return [.pause, .cancel]
        case .blocked:
            return [.resume, .cancel]
        case .draft, .planned, .assigned:
            return [.cancel]
        case .failed, .cancelled:
            return [.rerun]
        case .completed:
            return []
        }
    }
}

struct OperationsInspector: View {
    let tasks: [VoxTask]
    let agents: [Agent]
    let approvals: [PermissionApproval]
    let engineStatus: String
    let onControl: (VoxTask, EngineTaskAction) async throws -> Void

    @State private var nowExpanded = true
    @State private var queueExpanded = true
    @State private var historyExpanded = true
    @State private var hoveredTaskID: String?
    @State private var inFlightControl: String?
    @State private var controlError: String?

    private var groups: OperationsInspectorModel.Groups {
        OperationsInspectorModel.group(tasks)
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()

            ScrollView {
                VStack(spacing: 0) {
                    inspectorSection(
                        title: L("正在运行", "NOW RUNNING"),
                        count: groups.nowRunning.count,
                        isExpanded: $nowExpanded
                    ) {
                        taskRows(groups.nowRunning, empty: L("没有正在运行的任务", "No running work"))
                    }

                    inspectorSection(
                        title: L("队列与计划", "QUEUE & SCHEDULE"),
                        count: groups.queueAndSchedule.count,
                        isExpanded: $queueExpanded
                    ) {
                        taskRows(
                            groups.queueAndSchedule,
                            empty: L("没有排队或计划任务", "No queued or scheduled work")
                        )
                    }

                    inspectorSection(
                        title: L("历史与审计", "HISTORY & AUDIT"),
                        count: groups.historyAndAudit.count + approvals.count,
                        isExpanded: $historyExpanded
                    ) {
                        historyRows
                    }
                }
            }

            if let controlError {
                Divider()
                HStack(alignment: .top, spacing: TF.spacingSM) {
                    Image(systemName: "exclamationmark.circle")
                        .foregroundStyle(TF.danger)
                    Text(controlError)
                        .font(.system(size: 10))
                        .foregroundStyle(.primary)
                    Spacer(minLength: 0)
                    Button {
                        self.controlError = nil
                    } label: {
                        Image(systemName: "xmark")
                    }
                    .buttonStyle(.plain)
                    .help(L("关闭错误", "Dismiss error"))
                }
                .padding(TF.spacingMD)
                .accessibilityElement(children: .combine)
                .accessibilityLabel(L("任务控制失败：\(controlError)", "Task control failed: \(controlError)"))
            }
        }
        .background(TF.surface)
    }

    private var header: some View {
        HStack(spacing: TF.spacingSM) {
            VStack(alignment: .leading, spacing: 2) {
                Text(L("操作台", "OPERATIONS"))
                    .font(.system(size: 12, weight: .bold, design: .monospaced))
                Text(L("实时任务控制与审计", "Live task control and audit"))
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: TF.spacingSM)
            EngineStatusLabel(status: engineFunctionalStatus)
                .labelsHidden()
                .help(engineStatus)
        }
        .padding(.horizontal, TF.spacingMD)
        .frame(height: ChatLayoutMetrics.conversationToolbarHeight)
    }

    private var engineFunctionalStatus: TF.Status {
        switch engineStatus {
        case "running": return .running
        case "error": return .failed
        default: return .idle
        }
    }

    private func inspectorSection<Content: View>(
        title: String,
        count: Int,
        isExpanded: Binding<Bool>,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(spacing: 0) {
            Button {
                isExpanded.wrappedValue.toggle()
            } label: {
                HStack(spacing: TF.spacingSM) {
                    Image(systemName: isExpanded.wrappedValue ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.secondary)
                    Text(title)
                        .font(.system(size: 10, weight: .bold, design: .monospaced))
                        .foregroundStyle(.secondary)
                    Spacer(minLength: TF.spacingSM)
                    Text("\(count)")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(.tertiary)
                        .monospacedDigit()
                }
                .padding(.horizontal, TF.spacingMD)
                .frame(height: ChatLayoutMetrics.inspectorRowHeight)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(title), \(count)")
            .accessibilityValue(isExpanded.wrappedValue ? L("已展开", "Expanded") : L("已折叠", "Collapsed"))

            if isExpanded.wrappedValue {
                content()
            }

            Divider()
        }
    }

    @ViewBuilder
    private func taskRows(_ tasks: [VoxTask], empty: String) -> some View {
        if tasks.isEmpty {
            emptyRow(empty)
        } else {
            ForEach(tasks.prefix(12)) { task in
                taskRow(task)
            }
        }
    }

    @ViewBuilder
    private var historyRows: some View {
        if groups.historyAndAudit.isEmpty && approvals.isEmpty {
            emptyRow(L("还没有任务或权限审计记录", "No task or permission audit yet"))
        } else {
            ForEach(groups.historyAndAudit.prefix(12)) { task in
                taskRow(task)
            }
            ForEach(approvals.sorted(by: { $0.requestedAt > $1.requestedAt }).prefix(8)) { approval in
                approvalAuditRow(approval)
            }
        }
    }

    private func taskRow(_ task: VoxTask) -> some View {
        let controls = OperationsInspectorModel.controls(for: task.status)
        return HStack(spacing: TF.spacingSM) {
            Image(systemName: statusPresentation(task.status).symbol)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(statusPresentation(task.status).color)
                .frame(width: 12)
                .accessibilityHidden(true)

            Text(task.title)
                .font(.system(size: 11, weight: .medium))
                .lineLimit(1)

            Spacer(minLength: TF.spacingXS)

            if hoveredTaskID == task.id, !controls.isEmpty {
                HStack(spacing: 2) {
                    ForEach(controls, id: \.rawValue) { action in
                        controlButton(action, task: task)
                    }
                }
            } else {
                Text(statusPresentation(task.status).label)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, TF.spacingMD)
        .frame(minHeight: ChatLayoutMetrics.inspectorRowHeight)
        .contentShape(Rectangle())
        .onHover { hovering in
            hoveredTaskID = hovering ? task.id : nil
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(task.title), \(statusPresentation(task.status).label), \(agentName(task.assignedTo))")
    }

    private func controlButton(_ action: EngineTaskAction, task: VoxTask) -> some View {
        let controlID = "\(task.id)-\(action.rawValue)"
        return Button {
            guard inFlightControl == nil else { return }
            inFlightControl = controlID
            controlError = nil
            Task { @MainActor in
                do {
                    try await onControl(task, action)
                } catch {
                    controlError = error.localizedDescription
                }
                inFlightControl = nil
            }
        } label: {
            Group {
                if inFlightControl == controlID {
                    ProgressView()
                        .controlSize(.mini)
                } else {
                    Image(systemName: actionSymbol(action))
                }
            }
            .font(.system(size: 9, weight: .semibold))
            .frame(width: 22, height: 22)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(action == .cancel ? TF.danger : Color.secondary)
        .disabled(inFlightControl != nil)
        .help(actionLabel(action))
        .accessibilityLabel("\(actionLabel(action)): \(task.title)")
    }

    private func approvalAuditRow(_ approval: PermissionApproval) -> some View {
        let pending = approval.isPending && approval.expiresAt > Date()
        let denied = approval.decision == .denied
        let symbol = pending ? "exclamationmark" : denied ? "xmark.circle" : "checkmark.circle"
        let color = pending ? TF.warning : denied ? TF.idle : TF.success
        let label = pending
            ? L("待批准", "AWAITING")
            : approval.decision == .approved ? L("已批准", "APPROVED") : L("已拒绝", "DENIED")

        return HStack(spacing: TF.spacingSM) {
            Image(systemName: symbol)
                .foregroundStyle(color)
                .frame(width: 12)
                .accessibilityHidden(true)
            Text("\(agentName(approval.agentId)) · \(approval.tool)")
                .font(.system(size: 10, design: .monospaced))
                .lineLimit(1)
            Spacer(minLength: TF.spacingXS)
            Text(label)
                .font(.system(size: 9, design: .monospaced))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, TF.spacingMD)
        .frame(minHeight: ChatLayoutMetrics.inspectorRowHeight)
        .accessibilityElement(children: .combine)
    }

    private func emptyRow(_ label: String) -> some View {
        Text(label)
            .font(.system(size: 10))
            .foregroundStyle(.tertiary)
            .padding(.horizontal, TF.spacingMD)
            .frame(maxWidth: .infinity, minHeight: ChatLayoutMetrics.inspectorRowHeight, alignment: .leading)
    }

    private func statusPresentation(_ status: VoxTask.TaskStatus) -> (symbol: String, color: Color, label: String) {
        switch status {
        case .running: return ("play.fill", TF.accent, L("运行中", "RUNNING"))
        case .blocked: return ("pause.fill", TF.idle, L("已暂停", "PAUSED"))
        case .failed: return ("xmark", TF.danger, L("失败", "FAILED"))
        case .completed: return ("checkmark", TF.success, L("完成", "DONE"))
        case .cancelled: return ("slash.circle", TF.idle, L("已取消", "CANCELLED"))
        case .draft: return ("doc", TF.idle, L("草稿", "DRAFT"))
        case .planned: return ("calendar", TF.idle, L("计划", "PLANNED"))
        case .assigned: return ("clock", TF.idle, L("队列", "QUEUED"))
        }
    }

    private func actionSymbol(_ action: EngineTaskAction) -> String {
        switch action {
        case .pause: return "pause.fill"
        case .resume: return "play.fill"
        case .cancel: return "xmark"
        case .rerun: return "arrow.clockwise"
        }
    }

    private func actionLabel(_ action: EngineTaskAction) -> String {
        switch action {
        case .pause: return L("当前步骤后暂停", "Pause after current step")
        case .resume: return L("继续", "Resume")
        case .cancel: return L("取消", "Cancel")
        case .rerun: return L("重新运行", "Rerun")
        }
    }

    private func agentName(_ id: String) -> String {
        agents.first(where: { $0.id == id })?.name ?? id
    }
}
