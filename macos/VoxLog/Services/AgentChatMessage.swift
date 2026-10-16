import Foundation

struct AgentChatMessage: Identifiable, Hashable {
    let id = UUID()
    let taskId: String
    let agentId: String
    let type: AgentMessageType
    let content: String
    var tool: String?
    var toolOutput: String?
    let seq: Int
    let timestamp: Date
}

enum AgentMessageType: String, Codable {
    case text
    case toolUse = "tool_use"
    case toolResult = "tool_result"
    case status
    case error
}
