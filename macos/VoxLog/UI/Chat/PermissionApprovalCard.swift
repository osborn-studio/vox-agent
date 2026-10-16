import SwiftUI

/// A permission decision rendered in the conversation timeline.
/// Resolved requests remain in place as compact audit records.
struct PermissionApprovalCard: View {
    let approval: PermissionApproval
    let agentName: String
    let onDecision: (Bool, Bool) async -> Void

    @State private var isResolving = false

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            Group {
                if let decision = approval.decision {
                    auditLine(decision)
                } else if approval.expiresAt <= context.date {
                    expiredAuditLine
                } else {
                    requestCard(at: context.date)
                }
            }
            .id("permission-\(approval.id)")
            .accessibilityElement(children: .contain)
        }
    }

    private func requestCard(at now: Date) -> some View {
        HStack(spacing: 0) {
            Rectangle()
                .fill(TF.warning)
                .frame(width: 2)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: TF.spacingMD) {
                HStack(alignment: .top, spacing: TF.spacingSM) {
                    Image(systemName: "lock.shield")
                        .foregroundStyle(TF.warning)
                        .accessibilityHidden(true)

                    VStack(alignment: .leading, spacing: TF.spacingXS) {
                        Text(L(
                            "\(agentName) 请求权限",
                            "\(agentName) requests permission"
                        ))
                        .font(.system(size: 14, weight: .semibold))

                        Label(approval.tool, systemImage: "wrench.and.screwdriver")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }

                    Spacer(minLength: TF.spacingSM)

                    expiryLabel(at: now)
                }

                scopeList

                if let error = approval.resolutionError {
                    Label(error, systemImage: "exclamationmark.circle")
                        .font(.system(size: 11))
                        .foregroundStyle(TF.danger)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityLabel(L("审批失败：\(error)", "Decision failed: \(error)"))
                }

                HStack(spacing: TF.spacingSM) {
                    Spacer()

                    Button(L("拒绝", "Deny")) {
                        resolve(approved: false, rememberScope: false)
                    }
                    .buttonStyle(.plain)
                    .padding(.horizontal, TF.spacingMD)
                    .padding(.vertical, 7)
                    .background(
                        RoundedRectangle(cornerRadius: TF.cornerSM)
                            .stroke(TF.line, lineWidth: 1)
                    )
                    .disabled(isResolving)

                    if hasFileScope {
                        Button(L("始终允许此文件…", "Always for this file…")) {
                            resolve(approved: true, rememberScope: true)
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(TF.accent)
                        .padding(.horizontal, TF.spacingSM)
                        .padding(.vertical, 7)
                        .disabled(isResolving)
                        .accessibilityHint(L(
                            "以后允许这个 Agent 工具访问同一个文件",
                            "Allow this Agent tool to access the same file in the future"
                        ))
                    }

                    Button(L("批准", "Approve")) {
                        resolve(approved: true, rememberScope: false)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.white)
                    .padding(.horizontal, TF.spacingMD)
                    .padding(.vertical, 7)
                    .background(
                        RoundedRectangle(cornerRadius: TF.cornerSM)
                            .fill(TF.success)
                    )
                    .disabled(isResolving)
                }
            }
            .padding(TF.spacingMD)
        }
        .background(TF.surfaceRaised.opacity(0.55))
        .clipShape(RoundedRectangle(cornerRadius: TF.cornerMD))
        .overlay(
            RoundedRectangle(cornerRadius: TF.cornerMD)
                .stroke(TF.line.opacity(0.72), lineWidth: 1)
        )
        .accessibilityLabel(L(
            "\(agentName) 请求使用 \(approval.tool)",
            "\(agentName) requests permission to use \(approval.tool)"
        ))
    }

    private var scopeList: some View {
        VStack(alignment: .leading, spacing: TF.spacingSM) {
            Text(L("精确范围", "Exact scope"))
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.secondary)

            if approval.scope.isEmpty {
                Text(L("未提供范围", "No scope provided"))
                    .font(.system(size: 11))
                    .foregroundStyle(TF.danger)
            } else {
                ForEach(approval.scope.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                    VStack(alignment: .leading, spacing: TF.spacingXS) {
                        Text(key.replacingOccurrences(of: "_", with: " ").uppercased())
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(.secondary)
                        Text(value)
                            .font(.system(size: 11, design: .monospaced))
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, TF.spacingSM)
                            .padding(.vertical, 6)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(TF.surface)
                            .clipShape(RoundedRectangle(cornerRadius: TF.cornerSM))
                            .overlay(
                                RoundedRectangle(cornerRadius: TF.cornerSM)
                                    .stroke(TF.line.opacity(0.72), lineWidth: 1)
                            )
                    }
                }
            }
        }
    }

    private func expiryLabel(at now: Date) -> some View {
        let remaining = max(0, Int(approval.expiresAt.timeIntervalSince(now)))
        return Label(L("\(remaining) 秒", "\(remaining)s"), systemImage: "timer")
            .font(.system(size: 10, design: .monospaced))
            .foregroundStyle(.secondary)
            .monospacedDigit()
            .accessibilityLabel(L(
                "审批将在 \(remaining) 秒后过期",
                "Permission expires in \(remaining) seconds"
            ))
    }

    private var expiredAuditLine: some View {
        HStack(spacing: TF.spacingSM) {
            Image(systemName: "clock.badge.exclamationmark")
                .foregroundStyle(TF.idle)
                .accessibilityHidden(true)
            Text(L(
                "\(agentName) 的 \(approval.tool) 权限请求已过期",
                "\(agentName)'s \(approval.tool) permission request expired"
            ))
            .font(.system(size: 11, weight: .medium))
            Spacer(minLength: TF.spacingSM)
            Text(approval.expiresAt.formatted(date: .omitted, time: .shortened))
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, TF.spacingMD)
        .padding(.vertical, TF.spacingSM)
        .background(TF.surfaceRaised.opacity(0.36))
        .clipShape(RoundedRectangle(cornerRadius: TF.cornerSM))
        .accessibilityLabel(L("权限请求已过期", "Permission request expired"))
    }

    private func auditLine(_ decision: PermissionApproval.Decision) -> some View {
        let approved = decision == .approved
        return HStack(spacing: TF.spacingSM) {
            Image(systemName: approved ? "checkmark.circle.fill" : "xmark.circle")
                .foregroundStyle(approved ? TF.success : TF.idle)
                .accessibilityHidden(true)

            Text(L(
                "\(agentName) 的 \(approval.tool) 权限已\(approved ? "批准" : "拒绝")",
                "\(agentName)'s \(approval.tool) permission was \(decision.rawValue)"
            ))
            .font(.system(size: 11, weight: .medium))

            Spacer(minLength: TF.spacingSM)

            if let resolvedAt = approval.resolvedAt {
                Text(resolvedAt.formatted(date: .omitted, time: .shortened))
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, TF.spacingMD)
        .padding(.vertical, TF.spacingSM)
        .background(TF.surfaceRaised.opacity(0.36))
        .clipShape(RoundedRectangle(cornerRadius: TF.cornerSM))
        .accessibilityLabel(L(
            "\(agentName) 的 \(approval.tool) 权限已\(approved ? "批准" : "拒绝")",
            "\(agentName)'s \(approval.tool) permission was \(decision.rawValue)"
        ))
    }

    private var isExpired: Bool {
        approval.expiresAt <= Date()
    }

    private var hasFileScope: Bool {
        approval.scope["file_path"] != nil || approval.scope["path"] != nil
    }

    private func resolve(approved: Bool, rememberScope: Bool) {
        guard !isResolving, !isExpired else { return }
        isResolving = true
        Task { @MainActor in
            await onDecision(approved, rememberScope)
            isResolving = false
        }
    }
}
