import Foundation
import XCTest
@testable import VoxLog

final class EngineOperationsClientTests: XCTestCase {
    func testEngineTaskSnapshotMapsCanonicalIdentityAndLifecycle() throws {
        let json = """
        {
          "id": "engine-task-123",
          "agent_id": "coding",
          "title": "Ship the Mac control plane",
          "description": "Use the canonical Agent Engine snapshot.",
          "prompt": "Implement and verify it.",
          "status": "running",
          "priority": 2,
          "trigger_message_id": "message-456",
          "source_agent": "osborn",
          "created_at": "2026-07-19T07:00:00+00:00",
          "started_at": "2026-07-19T07:00:01+00:00",
          "completed_at": null,
          "error": null
        }
        """

        let snapshot = try JSONDecoder().decode(EngineTaskSnapshot.self, from: Data(json.utf8))
        let task = snapshot.voxTask

        XCTAssertEqual(task.id, "engine-task-123")
        XCTAssertEqual(task.assignedTo, "coding")
        XCTAssertEqual(task.status, .running)
        XCTAssertEqual(task.priority, .high)
        XCTAssertEqual(task.sourceMessageId, "message-456")
        XCTAssertEqual(task.sourceAgent, "osborn")
        XCTAssertEqual(task.description, "Use the canonical Agent Engine snapshot.")
    }

    func testTaskStoreReplacesLocalFacadeWithCanonicalEngineSnapshot() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let store = TaskStore(fileURL: directory.appendingPathComponent("tasks.json"))
        _ = await store.createTask(title: "Local placeholder", assignedTo: "general")
        let canonical = VoxTask(
            id: "engine-task-123",
            title: "Canonical task",
            status: .assigned,
            assignedTo: "coding"
        )

        await store.reconcileEngineTasks([canonical])

        let tasks = await store.allTasks()
        XCTAssertEqual(tasks.map(\.id), ["engine-task-123"])
        XCTAssertEqual(tasks.map(\.assignedTo), ["coding"])
    }

    func testSnapshotFetchCombinesCanonicalEngineResources() async throws {
        let client = EngineOperationsClient { request in
            let body: String
            switch request.url?.path {
            case "/v1/agents":
                body = """
                [{"id":"coding","name":"Code Master","emoji":"C","provider":"claude","parent_id":null,"external_system":null,"skills":["code"],"max_concurrent":2}]
                """
            case "/v1/tasks":
                body = """
                [{"id":"task-1","agent_id":"coding","title":"Build","description":"","prompt":"Build it","status":"assigned","priority":1,"trigger_message_id":null,"source_agent":"osborn","created_at":"2026-07-19T07:00:00+00:00","started_at":null,"completed_at":null,"error":null}]
                """
            case "/v1/permissions/pending":
                body = """
                [{"approval_id":"approval-1","task_id":"task-1","agent_id":"coding","tool":"write","scope":{"file_path":"/tmp/a"},"requested_at":"2026-07-19T07:00:00+00:00","expires_at":"2026-07-19T09:00:00+00:00"}]
                """
            default:
                throw EngineOperationsClientError.invalidResponse
            }
            let response = try XCTUnwrap(HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: nil,
                headerFields: nil
            ))
            return (Data(body.utf8), response)
        }

        let snapshot = try await client.fetchSnapshot(port: 18_900)

        XCTAssertEqual(snapshot.agents.map(\.id), ["coding"])
        XCTAssertEqual(snapshot.tasks.map(\.id), ["task-1"])
        XCTAssertEqual(snapshot.pendingApprovals.map(\.approvalId), ["approval-1"])
    }

    @MainActor
    func testPendingApprovalSnapshotReconcilesAfterReconnect() {
        let handler = EngineEventHandler()
        let requestedAt = Date()
        let snapshot = EnginePermissionSnapshot(
            approvalId: "approval-123",
            taskId: "engine-task-123",
            agentId: "coding",
            tool: "write",
            scope: ["file_path": "/tmp/DESIGN.md"],
            requestedAt: ISO8601DateFormatter().string(from: requestedAt),
            expiresAt: ISO8601DateFormatter().string(
                from: requestedAt.addingTimeInterval(120)
            )
        )

        handler.reconcilePendingApprovals([snapshot])

        XCTAssertEqual(handler.pendingApprovals.map(\.id), ["approval-123"])
        XCTAssertEqual(handler.pendingApprovals.first?.scope["file_path"], "/tmp/DESIGN.md")

        handler.reconcilePendingApprovals([])

        XCTAssertTrue(handler.pendingApprovals.isEmpty)
    }

    func testCommandRequestTargetsLocalMessageEndpointWithCanonicalBody() throws {
        let request = try EngineOperationsClient.makeCommandRequest(
            port: 18_900,
            text: "  Audit the release  ",
            agentId: "coding",
            provider: "claude"
        )

        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:18900/v1/message")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        let body = try XCTUnwrap(request.httpBody)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(json["text"] as? String, "Audit the release")
        XCTAssertEqual(json["agent_id"] as? String, "coding")
        XCTAssertEqual(json["provider"] as? String, "claude")
    }

    func testShippingChatUsesCanonicalOperationsInsteadOfFireAndForgetFacade() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let source = try String(
            contentsOf: packageRoot.appendingPathComponent("VoxLog/UI/Chat/ChatWindowView.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("engine.fetchOperations()"))
        XCTAssertTrue(source.contains("engine.sendCommand("))
        XCTAssertTrue(source.contains("reconcileEngineTasks"))
        XCTAssertTrue(source.contains("reconcilePendingApprovals"))
        XCTAssertFalse(source.contains("private func sendToOrchestrator"))
    }
}
