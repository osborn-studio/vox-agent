import Foundation
import XCTest
@testable import VoxLog

final class PermissionApprovalTests: XCTestCase {
    func testPermissionRequestPayloadDecodesExactScopeAndExpiry() throws {
        let json = """
        {
          "type": "permission:request",
          "payload": {
            "approval_id": "approval-123",
            "task_id": "task-456",
            "agent_id": "codex",
            "tool": "bash",
            "scope": {
              "command": "git status --short",
              "working_directory": "/tmp/project"
            },
            "requested_at": "2026-07-18T19:00:00+00:00",
            "expires_at": "2026-07-18T19:02:00+00:00"
          }
        }
        """

        let event = try JSONDecoder().decode(EngineEvent.self, from: Data(json.utf8))

        XCTAssertEqual(event.payload.approvalId, "approval-123")
        XCTAssertEqual(event.payload.taskId, "task-456")
        XCTAssertEqual(event.payload.agentId, "codex")
        XCTAssertEqual(event.payload.tool, "bash")
        XCTAssertEqual(event.payload.scope?["command"], "git status --short")
        XCTAssertEqual(event.payload.scope?["working_directory"], "/tmp/project")
        XCTAssertEqual(event.payload.requestedAt, "2026-07-18T19:00:00+00:00")
        XCTAssertEqual(event.payload.expiresAt, "2026-07-18T19:02:00+00:00")
    }

    func testHandlerKeepsResolvedRequestAsInPlaceAuditRecord() async throws {
        let handler = EngineEventHandler()
        let sender = ApprovalSenderSpy()
        handler.approvalSender = sender
        handler.handle(makePermissionEvent())

        XCTAssertEqual(handler.permissionRequests.count, 1)
        XCTAssertEqual(handler.pendingApprovals.map(\.id), ["approval-123"])
        XCTAssertEqual(handler.permissionRequests[0].scope["command"], "git status --short")

        let resolved = await handler.resolveApproval(id: "approval-123", approved: true)

        XCTAssertTrue(resolved)
        XCTAssertEqual(sender.decisions, [
            .init(id: "approval-123", approved: true, rememberScope: false),
        ])
        XCTAssertTrue(handler.pendingApprovals.isEmpty)
        XCTAssertEqual(handler.permissionRequests[0].decision, .approved)
        XCTAssertNotNil(handler.permissionRequests[0].resolvedAt)
        XCTAssertNil(handler.permissionRequests[0].resolutionError)
    }

    func testHandlerIgnoresDuplicateRequestAndKeepsFailureActionable() async {
        let handler = EngineEventHandler()
        let sender = ApprovalSenderSpy(error: ApprovalSenderSpy.TestError.rejected)
        handler.approvalSender = sender
        let event = makePermissionEvent()
        handler.handle(event)
        handler.handle(event)

        XCTAssertEqual(handler.permissionRequests.count, 1)

        let resolved = await handler.resolveApproval(id: "approval-123", approved: false)

        XCTAssertFalse(resolved)
        XCTAssertEqual(handler.pendingApprovals.map(\.id), ["approval-123"])
        XCTAssertNil(handler.permissionRequests[0].decision)
        XCTAssertNotNil(handler.permissionRequests[0].resolutionError)
    }

    func testApprovalRequestUsesRESTEndpointAndJSONBody() throws {
        let request = try EngineWebSocket.makeApprovalRequest(
            port: 18_900,
            approvalId: "approval-123",
            approved: false,
            rememberScope: true
        )

        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:18900/v1/permissions/approve")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")

        let body = try XCTUnwrap(request.httpBody)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(json["approval_id"] as? String, "approval-123")
        XCTAssertEqual(json["approved"] as? Bool, false)
        XCTAssertEqual(json["remember_scope"] as? Bool, true)
    }

    func testPermissionSurfaceIsInlineAndNeverAModalOrBlindApproval() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let chatWindow = try String(
            contentsOf: packageRoot.appendingPathComponent("VoxLog/UI/Chat/ChatWindowView.swift"),
            encoding: .utf8
        )
        let card = try String(
            contentsOf: packageRoot.appendingPathComponent("VoxLog/UI/Chat/PermissionApprovalCard.swift"),
            encoding: .utf8
        )
        let timelineTurn = try String(
            contentsOf: packageRoot.appendingPathComponent("VoxLog/UI/Chat/AgentTimelineTurn.swift"),
            encoding: .utf8
        )
        let websocket = try String(
            contentsOf: packageRoot.appendingPathComponent("VoxLog/Services/EngineWebSocket.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(chatWindow.contains("PermissionApprovalCard("))
        XCTAssertFalse(chatWindow.contains("pendingApprovals.first"))
        XCTAssertFalse(timelineTurn.contains("PermissionApprovalSheet"))
        XCTAssertTrue(card.contains("TF.warning"))
        XCTAssertTrue(card.contains("approval.scope.sorted"))
        XCTAssertTrue(card.contains("Approve"))
        XCTAssertTrue(card.contains("Deny"))
        XCTAssertTrue(card.contains("Always for this file"))
        XCTAssertTrue(card.contains("accessibility"))
        XCTAssertFalse(card.contains("withAnimation"))
        XCTAssertFalse(websocket.contains("permission:response"))
    }

    private func makePermissionEvent() -> EngineEvent {
        let requestedAt = Date()
        let expiresAt = requestedAt.addingTimeInterval(120)
        return EngineEvent(
            type: "permission:request",
            payload: EngineEventPayload(
                taskId: "task-456",
                tool: "bash",
                approvalId: "approval-123",
                agentId: "codex",
                scope: ["command": "git status --short"],
                requestedAt: ISO8601DateFormatter().string(from: requestedAt),
                expiresAt: ISO8601DateFormatter().string(from: expiresAt)
            )
        )
    }
}

private final class ApprovalSenderSpy: ApprovalDecisionSending {
    struct Decision: Equatable {
        let id: String
        let approved: Bool
        let rememberScope: Bool
    }

    enum TestError: Error {
        case rejected
    }

    private let error: Error?
    private(set) var decisions: [Decision] = []

    init(error: Error? = nil) {
        self.error = error
    }

    func sendApproval(
        approvalId: String,
        approved: Bool,
        rememberScope: Bool
    ) async throws {
        decisions.append(.init(
            id: approvalId,
            approved: approved,
            rememberScope: rememberScope
        ))
        if let error {
            throw error
        }
    }
}
