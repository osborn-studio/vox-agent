import Foundation

protocol ApprovalDecisionSending: AnyObject {
    func sendApproval(
        approvalId: String,
        approved: Bool,
        rememberScope: Bool
    ) async throws
}

enum ApprovalDecisionError: LocalizedError {
    case engineUnavailable
    case invalidResponse
    case rejected(statusCode: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .engineUnavailable:
            return L("Agent 引擎尚未连接", "Agent engine is not connected")
        case .invalidResponse:
            return L("Agent 引擎返回了无效响应", "Agent engine returned an invalid response")
        case .rejected(let statusCode, let message):
            return L(
                "Agent 引擎拒绝了审批（\(statusCode)）：\(message)",
                "Agent engine rejected the decision (\(statusCode)): \(message)"
            )
        }
    }
}

/// Connects to the Python Vox Agents Engine via WebSocket.
/// Receives real-time task execution events and dispatches them to the UI.
///
/// Event flow:
/// ```
/// Python Engine → WebSocket → EngineWebSocket → EngineEventHandler → SwiftUI
/// ```
@Observable
final class EngineWebSocket {

    // MARK: - State

    enum ConnectionState: String {
        case disconnected
        case connecting
        case connected
        case error
    }

    private(set) var state: ConnectionState = .disconnected
    private(set) var lastError: String?

    /// Callback for incoming engine events.
    var onEvent: ((EngineEvent) -> Void)?

    // MARK: - Private

    private var task: URLSessionWebSocketTask?
    private var session: URLSession?
    private var baseURL: String = "ws://127.0.0.1"
    private var port: Int = 0
    private var reconnectAttempts = 0
    private let maxReconnectAttempts = 10
    private var isIntentionalDisconnect = false

    // MARK: - Connect / Disconnect

    func connect(port: Int) {
        self.port = port
        self.isIntentionalDisconnect = false
        self.reconnectAttempts = 0
        _connect()
    }

    func disconnect() {
        isIntentionalDisconnect = true
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        state = .disconnected
    }

    /// Resolve a permission request through the engine's decision endpoint.
    /// The WebSocket is server-to-client only and never carries decisions.
    func sendApproval(
        approvalId: String,
        approved: Bool,
        rememberScope: Bool
    ) async throws {
        guard port > 0 else { throw ApprovalDecisionError.engineUnavailable }

        let request = try Self.makeApprovalRequest(
            port: port,
            approvalId: approvalId,
            approved: approved,
            rememberScope: rememberScope
        )
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ApprovalDecisionError.invalidResponse
        }
        guard (200...299).contains(http.statusCode) else {
            let message = (try? JSONDecoder().decode(EngineErrorResponse.self, from: data).detail)
                ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw ApprovalDecisionError.rejected(statusCode: http.statusCode, message: message)
        }
        guard let result = try? JSONDecoder().decode(ApprovalDecisionResponse.self, from: data),
              result.ok else {
            throw ApprovalDecisionError.invalidResponse
        }
    }

    static func makeApprovalRequest(
        port: Int,
        approvalId: String,
        approved: Bool,
        rememberScope: Bool
    ) throws -> URLRequest {
        guard let url = URL(string: "http://127.0.0.1:\(port)/v1/permissions/approve") else {
            throw ApprovalDecisionError.engineUnavailable
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(ApprovalDecisionRequest(
            approvalId: approvalId,
            approved: approved,
            rememberScope: rememberScope
        ))
        return request
    }

    // MARK: - Internal

    private func _connect() {
        guard let url = URL(string: "\(baseURL):\(port)/ws") else {
            state = .error
            lastError = "invalid URL"
            return
        }

        state = .connecting
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = true
        session = URLSession(configuration: config)
        task = session?.webSocketTask(with: url)
        task?.resume()

        state = .connected
        reconnectAttempts = 0
        _receiveLoop()
    }

    private func _receiveLoop() {
        task?.receive { [weak self] result in
            guard let self = self else { return }

            switch result {
            case .success(let message):
                switch message {
                case .data(let data):
                    self._handleMessage(data)
                case .string(let text):
                    if let data = text.data(using: .utf8) {
                        self._handleMessage(data)
                    }
                @unknown default:
                    break
                }
                // continue receiving
                self._receiveLoop()

            case .failure(let error):
                self.lastError = error.localizedDescription
                self.state = .error
                if !self.isIntentionalDisconnect {
                    self._scheduleReconnect()
                }
            }
        }
    }

    private func _handleMessage(_ data: Data) {
        guard let event = try? JSONDecoder().decode(EngineEvent.self, from: data) else {
            return
        }
        DispatchQueue.main.async { [weak self] in
            self?.onEvent?(event)
        }
    }

    private func _scheduleReconnect() {
        guard reconnectAttempts < maxReconnectAttempts else {
            state = .disconnected
            lastError = "max reconnect attempts reached"
            return
        }
        reconnectAttempts += 1
        let delay = min(Double(reconnectAttempts) * 2.0, 30.0)  // exponential, cap 30s
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?._connect()
        }
    }
}

extension EngineWebSocket: ApprovalDecisionSending {}

private struct ApprovalDecisionRequest: Encodable {
    let approvalId: String
    let approved: Bool
    let rememberScope: Bool

    enum CodingKeys: String, CodingKey {
        case approvalId = "approval_id"
        case approved
        case rememberScope = "remember_scope"
    }
}

private struct ApprovalDecisionResponse: Decodable {
    let ok: Bool
    let decision: String
}

private struct EngineErrorResponse: Decodable {
    let detail: String
}

// MARK: - Event Models

/// Incoming event from the Python engine.
struct EngineEvent: Codable {
    let type: String     // "task:message", "task:progress", "task:completed", etc.
    let payload: EngineEventPayload
}

/// Flexible payload that handles multiple event types.
struct EngineEventPayload: Codable {
    // Task events
    var taskId: String?
    var status: String?
    var seq: Int?

    // Message events
    var authorType: String?
    var authorId: String?
    var messageType: String?   // "text", "tool_use", "tool_result", "error"
    var content: String?
    var tool: String?
    var output: String?

    // Permission requests
    var approvalId: String?
    var agentId: String?
    var scope: [String: String]?
    var requestedAt: String?
    var expiresAt: String?

    // Progress
    var step: Int?
    var total: Int?
    var summary: String?

    // Error
    var error: String?

    // Result
    var result: [String: String]?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case status, seq
        case authorType = "author_type"
        case authorId = "author_id"
        case messageType = "message_type"
        case content, tool, output
        case approvalId = "approval_id"
        case agentId = "agent_id"
        case scope
        case requestedAt = "requested_at"
        case expiresAt = "expires_at"
        case step, total, summary, error, result
    }
}
