import Foundation

enum EngineOperationsClientError: LocalizedError {
    case engineUnavailable
    case invalidPort
    case emptyCommand
    case commandTooLong
    case invalidResponse
    case rejected(statusCode: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .engineUnavailable:
            return L("Agent 引擎尚未连接", "Agent Engine is not connected")
        case .invalidPort:
            return L("Agent 引擎端口无效", "Agent Engine port is invalid")
        case .emptyCommand:
            return L("Agent 指令不能为空", "Agent command must not be empty")
        case .commandTooLong:
            return L("Agent 指令超过 8,000 字符", "Agent command exceeds 8,000 characters")
        case .invalidResponse:
            return L("Agent 引擎返回了无效响应", "Agent Engine returned an invalid response")
        case .rejected(let statusCode, let message):
            return L(
                "Agent 引擎拒绝了请求（\(statusCode)）：\(message)",
                "Agent Engine rejected the request (\(statusCode)): \(message)"
            )
        }
    }
}

struct EngineAgentSnapshot: Decodable, Equatable {
    let id: String
    let name: String
    let emoji: String
    let provider: String
    let parentId: String?
    let externalSystem: String?
    let skills: [String]
    let maxConcurrent: Int

    enum CodingKeys: String, CodingKey {
        case id, name, emoji, provider, skills
        case parentId = "parent_id"
        case externalSystem = "external_system"
        case maxConcurrent = "max_concurrent"
    }

    var agent: Agent {
        Agent(
            id: id,
            name: name,
            emoji: emoji,
            agentType: .systemDefault,
            parentId: parentId,
            externalSystem: externalSystem,
            bindingStatus: externalSystem == nil ? .unbound : .bound
        )
    }
}

struct EngineTaskSnapshot: Decodable, Equatable {
    let id: String
    let agentId: String
    let title: String
    let description: String?
    let prompt: String?
    let status: String
    let priority: Int
    let triggerMessageId: String?
    let sourceAgent: String?
    let createdAt: String
    let startedAt: String?
    let completedAt: String?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case id, title, description, prompt, status, priority, error
        case agentId = "agent_id"
        case triggerMessageId = "trigger_message_id"
        case sourceAgent = "source_agent"
        case createdAt = "created_at"
        case startedAt = "started_at"
        case completedAt = "completed_at"
    }

    var voxTask: VoxTask {
        let created = EngineTimestamp.parse(createdAt) ?? Date()
        let updated = EngineTimestamp.parse(completedAt)
            ?? EngineTimestamp.parse(startedAt)
            ?? created
        return VoxTask(
            id: id,
            title: title,
            description: description?.nilIfEmpty ?? prompt ?? "",
            status: VoxTask.TaskStatus(rawValue: status) ?? .draft,
            priority: Self.taskPriority(priority),
            assignedTo: agentId,
            sourceMessageId: triggerMessageId,
            sourceAgent: sourceAgent,
            createdAt: created,
            updatedAt: updated
        )
    }

    private static func taskPriority(_ priority: Int) -> VoxTask.TaskPriority {
        switch priority {
        case ...0: return .low
        case 1: return .medium
        case 2: return .high
        default: return .urgent
        }
    }
}

struct EnginePermissionSnapshot: Decodable, Equatable {
    let approvalId: String
    let taskId: String
    let agentId: String
    let tool: String
    let scope: [String: String]
    let requestedAt: String
    let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case tool, scope
        case approvalId = "approval_id"
        case taskId = "task_id"
        case agentId = "agent_id"
        case requestedAt = "requested_at"
        case expiresAt = "expires_at"
    }

    var approval: PermissionApproval {
        let requested = EngineTimestamp.parse(requestedAt) ?? Date()
        return PermissionApproval(
            id: approvalId,
            taskId: taskId,
            agentId: agentId,
            tool: tool,
            scope: scope,
            requestedAt: requested,
            expiresAt: EngineTimestamp.parse(expiresAt)
                ?? requested.addingTimeInterval(120)
        )
    }
}

struct EngineOperationsSnapshot: Equatable {
    let agents: [EngineAgentSnapshot]
    let tasks: [EngineTaskSnapshot]
    let pendingApprovals: [EnginePermissionSnapshot]
}

struct EngineCommandResponse: Decodable, Equatable {
    let action: String
    let message: String
    let taskId: String?

    enum CodingKeys: String, CodingKey {
        case action, message
        case taskId = "task_id"
    }
}

struct EngineOperationsClient {
    typealias DataLoader = (URLRequest) async throws -> (Data, URLResponse)

    private struct CommandBody: Encodable {
        let workingDirectory: String?
        let text: String
        let agentId: String?
        let provider: String

        enum CodingKeys: String, CodingKey {
            case workingDirectory = "working_directory"
            case text, provider
            case agentId = "agent_id"
        }
    }

    private let loadData: DataLoader

    init(session: URLSession = .shared) {
        self.loadData = { request in
            try await session.data(for: request)
        }
    }

    init(dataLoader: @escaping DataLoader) {
        self.loadData = dataLoader
    }

    func fetchSnapshot(port: Int, bearerToken: String? = nil) async throws -> EngineOperationsSnapshot {
        async let agents: [EngineAgentSnapshot] = get(
            port: port, path: "/v1/agents", bearerToken: bearerToken
        )
        async let tasks: [EngineTaskSnapshot] = get(
            port: port, path: "/v1/tasks", bearerToken: bearerToken
        )
        async let approvals: [EnginePermissionSnapshot] = get(
            port: port, path: "/v1/permissions/pending", bearerToken: bearerToken
        )
        return try await EngineOperationsSnapshot(
            agents: agents,
            tasks: tasks,
            pendingApprovals: approvals
        )
    }

    func sendCommand(
        port: Int,
        text: String,
        agentId: String?,
        provider: String = "claude",
        bearerToken: String? = nil,
        workingDirectory: String? = nil
    ) async throws -> EngineCommandResponse {
        var request = try Self.makeCommandRequest(
            port: port,
            text: text,
            agentId: agentId,
            provider: provider,
            workingDirectory: workingDirectory
        )
        Self.authorize(&request, bearerToken: bearerToken)
        return try await perform(request)
    }

    static func makeCommandRequest(
        port: Int,
        text: String,
        agentId: String?,
        provider: String,
        workingDirectory: String? = nil
    ) throws -> URLRequest {
        let command = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !command.isEmpty else { throw EngineOperationsClientError.emptyCommand }
        guard command.count <= 8_000 else { throw EngineOperationsClientError.commandTooLong }
        var request = try makeRequest(port: port, path: "/v1/message")
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(CommandBody(
            workingDirectory: workingDirectory,
            text: command,
            agentId: agentId?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
            provider: provider.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty ?? "claude"
        ))
        return request
    }

    private func get<T: Decodable>(
        port: Int,
        path: String,
        bearerToken: String?
    ) async throws -> T {
        var request = try Self.makeRequest(port: port, path: path)
        Self.authorize(&request, bearerToken: bearerToken)
        return try await perform(request)
    }

    private func perform<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await loadData(request)
        guard let http = response as? HTTPURLResponse else {
            throw EngineOperationsClientError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONDecoder().decode(EngineOperationErrorResponse.self, from: data).detail)
                ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw EngineOperationsClientError.rejected(statusCode: http.statusCode, message: detail)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw EngineOperationsClientError.invalidResponse
        }
    }

    private static func makeRequest(port: Int, path: String) throws -> URLRequest {
        guard (1...65_535).contains(port),
              let url = URL(string: "http://127.0.0.1:\(port)\(path)") else {
            throw EngineOperationsClientError.invalidPort
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 12
        return request
    }

    private static func authorize(_ request: inout URLRequest, bearerToken: String?) {
        guard let token = bearerToken?.trimmingCharacters(in: .whitespacesAndNewlines),
              !token.isEmpty else { return }
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    }
}

private struct EngineOperationErrorResponse: Decodable {
    let detail: String
}

private enum EngineTimestamp {
    static func parse(_ value: String?) -> Date? {
        guard let value else { return nil }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
