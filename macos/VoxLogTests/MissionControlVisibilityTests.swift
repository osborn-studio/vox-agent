import Foundation
import XCTest
@testable import VoxLog

final class MissionControlVisibilityTests: XCTestCase {
    func testPreferenceDefaultsOffAndHonorsStoredValue() {
        let name = "MissionControlTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        XCTAssertFalse(MissionControlNavigation.isEnabled(in: defaults))
        XCTAssertEqual(MissionControlNavigation.initialWorkspace(defaults: defaults), .command)
        defaults.set(true, forKey: MissionControlNavigation.preferenceKey)
        XCTAssertTrue(MissionControlNavigation.isEnabled(in: defaults))
        XCTAssertEqual(MissionControlNavigation.initialWorkspace(defaults: defaults), .missionControl)
    }

    func testVisibilityChangesNeverStealTheConversation() {
        XCTAssertEqual(MissionControlNavigation.workspace(.missionControl, enabled: false), .command)
        XCTAssertEqual(MissionControlNavigation.workspace(.command, enabled: false), .command)
        XCTAssertEqual(MissionControlNavigation.workspace(.command, enabled: true), .command)
        XCTAssertEqual(MissionControlNavigation.workspace(.missionControl, enabled: true), .missionControl)
    }

    func testApprovalRoutesToItsAgentAndConsumesRequestIncludingRepeatedFocus() {
        let now = Date(timeIntervalSince1970: 100)
        let approval = makeApproval(expiresAt: now.addingTimeInterval(30))
        for _ in 0..<2 {
            var request: String? = "approval"
            let route = MissionControlNavigation.consumeApprovalFocus(
                request: &request, approvals: [approval], now: now
            )
            XCTAssertEqual(route?.agentID, "coder")
            XCTAssertEqual(route?.workspace, .command)
            XCTAssertEqual(route?.approvalID, "approval")
            XCTAssertNil(request)
        }
    }

    func testUnknownExpiredAndResolvedRequestsAreConsumedWithoutRouting() {
        let now = Date(timeIntervalSince1970: 100)
        for approvals: [PermissionApproval] in [
            [], [makeApproval(expiresAt: now)],
            [makeApproval(expiresAt: now.addingTimeInterval(30), decision: .approved)],
            [makeApproval(expiresAt: now.addingTimeInterval(30), decision: .denied)],
        ] {
            var request: String? = "approval"
            XCTAssertNil(MissionControlNavigation.consumeApprovalFocus(
                request: &request, approvals: approvals, now: now
            ))
            XCTAssertNil(request)
        }
        var request: String?
        XCTAssertNil(MissionControlNavigation.consumeApprovalFocus(
            request: &request, approvals: [], now: now
        ))
    }

    func testAutoFollowReleasesResolvedExpiredAndMissingApproval() {
        let now = Date(timeIntervalSince1970: 100)
        XCTAssertEqual(MissionControlNavigation.pendingFocusID("approval", approvals: [
            makeApproval(expiresAt: now.addingTimeInterval(30))
        ], now: now), "approval")
        for approvals: [PermissionApproval] in [
            [], [makeApproval(expiresAt: now)],
            [makeApproval(expiresAt: now.addingTimeInterval(30), decision: .approved)],
            [makeApproval(expiresAt: now.addingTimeInterval(30), decision: .denied)],
        ] {
            XCTAssertNil(MissionControlNavigation.pendingFocusID("approval", approvals: approvals, now: now))
        }
    }

    // Wiring checks supplement the behavioral tests; they do not replace GUI acceptance.
    func testColdWindowAndRepeatedFocusWiring() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        let window = try String(contentsOf: root.appendingPathComponent("VoxLog/UI/Chat/ChatWindowView.swift"), encoding: .utf8)
        let app = try String(contentsOf: root.appendingPathComponent("VoxLog/VoxLogApp.swift"), encoding: .utf8)
        let sidebar = try String(contentsOf: root.appendingPathComponent("VoxLog/UI/Chat/ChatSidebar.swift"), encoding: .utf8)
        let menuRoute = app.components(separatedBy: "private func focusPendingApproval()")[1]
        XCTAssertTrue(menuRoute.range(of: "appState.pendingApprovalFocusID = approval.id")!.lowerBound
                      < menuRoute.range(of: "openSettingsWindow(id:")!.lowerBound)
        let consume = window.components(separatedBy: "private func consumePendingApprovalFocus()")[1]
        XCTAssertTrue(consume.range(of: "guard isNavigationReady else { return }")!.lowerBound
                      < consume.range(of: "MissionControlNavigation.consumeApprovalFocus(")!.lowerBound)
        XCTAssertTrue(window.contains(".onChange(of: approvalFocusRevision)"))
        XCTAssertTrue(window.contains("approvalFocusRevision &+= 1"))
        XCTAssertTrue(window.contains("guard requestedAgent == selection.localHistoryID, !Task.isCancelled else { return }"))
        XCTAssertTrue(window.contains("if pendingApprovalScrollID != nil { approvalFocusRevision &+= 1 }"))
        XCTAssertTrue(window.contains("if let pendingApprovalScrollID {"))
        XCTAssertTrue(window.contains("approvalConversationAgentID != selectedAgent"))
        XCTAssertTrue(window.contains(".filter { $0.agentId == selectedAgent }"))
        let workspace = sidebar.components(separatedBy: "if showMissionControl {")[1]
            .components(separatedBy: "ScrollView {")[0]
        XCTAssertTrue(workspace.contains(".keyboardShortcut(\"1\", modifiers: [.command])"))
        XCTAssertTrue(workspace.contains(".keyboardShortcut(\"2\", modifiers: [.command])"))
    }

    private func makeApproval(expiresAt: Date, decision: PermissionApproval.Decision? = nil) -> PermissionApproval {
        PermissionApproval(id: "approval", taskId: "task", agentId: "coder", tool: "filesystem.read",
                           scope: ["path": "/example"], requestedAt: Date(timeIntervalSince1970: 90),
                           expiresAt: expiresAt, decision: decision)
    }
}
