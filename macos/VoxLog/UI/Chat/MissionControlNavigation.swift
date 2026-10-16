import Foundation

/// Shared preference and deterministic navigation rules, independent of window lifetime.
enum MissionControlNavigation {
    static let preferenceKey = "voxagent_show_mission_control"

    static func isEnabled(in defaults: UserDefaults = .standard) -> Bool {
        defaults.bool(forKey: preferenceKey)
    }

    static func initialWorkspace(defaults: UserDefaults = .standard) -> AgentWorkspace {
        isEnabled(in: defaults) ? .missionControl : .command
    }

    static func workspace(_ current: AgentWorkspace, enabled: Bool) -> AgentWorkspace {
        enabled ? current : .command
    }

    /// Auto-follow returns to the conversation once this request expires or resolves.
    static func pendingFocusID(
        _ id: String?, approvals: [PermissionApproval], now: Date = Date()
    ) -> String? {
        approvals.first { $0.id == id && $0.isPending && $0.expiresAt > now }?.id
    }

    struct ApprovalRoute {
        let agentID: String
        let approvalID: String
        let workspace: AgentWorkspace = .command
    }

    static func consumeApprovalFocus(
        request: inout String?, approvals: [PermissionApproval], now: Date = Date()
    ) -> ApprovalRoute? {
        defer { request = nil }
        guard let id = request,
              let approval = approvals.first(where: {
                  $0.id == id && $0.isPending && $0.expiresAt > now
              }) else { return nil }
        return ApprovalRoute(agentID: approval.agentId, approvalID: approval.id)
    }
}
