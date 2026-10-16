import SwiftUI

struct AgentStatusStrip: View {
    let snapshot: AgentStatusSnapshot

    var body: some View {
        HStack(spacing: TF.spacingSM) {
            StatusCountsRow(snapshot: snapshot)

            Spacer(minLength: TF.spacingXS)

            EngineStatusLabel(status: snapshot.engineStatus)
        }
        .frame(height: ChatLayoutMetrics.statusStripHeight)
        .padding(.horizontal, TF.spacingMD)
        .background(TF.surface)
    }
}

struct StatusCountsRow: View {
    let snapshot: AgentStatusSnapshot
    var onAwaitingApproval: (() -> Void)?

    init(
        snapshot: AgentStatusSnapshot,
        onAwaitingApproval: (() -> Void)? = nil
    ) {
        self.snapshot = snapshot
        self.onAwaitingApproval = onAwaitingApproval
    }

    var body: some View {
        HStack(spacing: TF.spacingSM) {
            StatusCountLabel(
                status: .running,
                count: snapshot.runningCount,
                label: L("运行中", "running")
            )
            StatusCountLabel(
                status: .idle,
                count: snapshot.queuedCount,
                label: L("队列中", "queued")
            )
            if snapshot.awaitingApprovalCount > 0, let onAwaitingApproval {
                Button(action: onAwaitingApproval) {
                    awaitingApprovalLabel
                }
                .buttonStyle(.plain)
                .help(L("定位到待批准权限", "Show pending permission"))
            } else {
                awaitingApprovalLabel
            }
            StatusCountLabel(
                status: .failed,
                count: snapshot.failedCount,
                label: L("失败", "failed")
            )
        }
    }

    private var awaitingApprovalLabel: some View {
        StatusCountLabel(
            status: .awaitingApproval,
            count: snapshot.awaitingApprovalCount,
            label: L("待批准", "awaiting")
        )
    }
}

struct EngineStatusLabel: View {
    let status: TF.Status

    var body: some View {
        Label(label, systemImage: status.symbolName)
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(.secondary)
            .labelStyle(.titleAndIcon)
            .accessibilityLabel(accessibilityLabel)
    }

    private var label: String {
        switch status {
        case .running: return L("引擎已连接", "engine connected")
        case .failed: return L("引擎错误", "engine error")
        case .idle: return L("引擎未连接", "engine offline")
        case .awaitingApproval, .completed: return status.label
        }
    }

    private var accessibilityLabel: String {
        L("Agent 引擎状态：\(label)", "Agent engine status: \(label)")
    }
}

struct StatusCountLabel: View {
    let status: TF.Status
    let count: Int
    let label: String

    var body: some View {
        HStack(spacing: TF.spacingXS) {
            Circle()
                .fill(status.color)
                .frame(width: 6, height: 6)
                .accessibilityHidden(true)
            Text("\(count) \(label)")
                .font(.system(size: 11, design: .monospaced))
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(count) \(label)")
    }
}
