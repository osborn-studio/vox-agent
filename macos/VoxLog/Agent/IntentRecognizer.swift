import Foundation
import os

/// Recognizes intent from forwarded messages to create tasks.
///
/// When a user forwards a message to an agent with instructions,
/// the IntentRecognizer uses LLM to parse the intent into a structured task.
///
/// Example:
///   Forward "这段代码有个 bug" to Claude Code agent
///   → Task(title: "修复 bug", assignedTo: "claude-code", description: "这段代码有个 bug")
actor IntentRecognizer {

    static let shared = IntentRecognizer()
    private let logger = Logger(subsystem: "com.voxlog.intent", category: "IntentRecognizer")

    /// Parse a message into a task using LLM.
    /// Returns a task title + description, or nil if the message isn't a command.
    func parseIntent(message: String, targetAgent: String) async -> (title: String, description: String, priority: VoxTask.TaskPriority)? {
        // Quick heuristic: check if message contains command-like patterns
        let commandPatterns = ["帮我", "请", "把", "修", "改", "做", "写", "查", "找", "分析",
                               "review", "fix", "check", "help", "please", "do", "write", "find"]
        let hasCommand = commandPatterns.contains { message.lowercased().contains($0) }

        if !hasCommand {
            // Not a command, just a forwarded message
            return nil
        }

        // Try LLM intent parsing
        if let llmResult = await parseLLMIntent(message: message) {
            return llmResult
        }

        // Fallback: use first sentence as title
        let title = extractTitle(from: message)
        let priority = detectPriority(from: message)
        return (title: title, description: message, priority: priority)
    }

    // MARK: - LLM Intent Parsing

    private func parseLLMIntent(message: String) async -> (title: String, description: String, priority: VoxTask.TaskPriority)? {
        guard let providerConfig = KeychainService.loadSelectedLLMConfig() else { return nil }
        let config = providerConfig.toLLMConfig()

        let prompt = """
        从以下语音消息中提取任务信息。返回 JSON 格式：
        {"title": "简短标题", "description": "详细描述", "priority": "medium"}
        priority 可选值: low, medium, high, urgent
        如果不是任务指令，返回 {"title": "", "description": "", "priority": "medium"}

        消息: \(message)
        """

        do {
            let client = DoubaoChatClient(provider: KeychainService.selectedLLMProvider)
            let result = try await client.process(text: message, prompt: prompt, config: config)

            if let data = result.data(using: String.Encoding.utf8),
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: String],
               let title = json["title"], !title.isEmpty {
                let priority = VoxTask.TaskPriority(rawValue: json["priority"] ?? "medium") ?? .medium
                return (title: title, description: json["description"] ?? message, priority: priority)
            }
        } catch {
            logger.warning("LLM intent parsing failed: \(error)")
        }

        return nil
    }

    // MARK: - Heuristic Fallback

    private func extractTitle(from message: String) -> String {
        // Take first sentence or first 30 chars
        let separators = CharacterSet(charactersIn: "。.！!？?\n")
        let firstSentence = message.components(separatedBy: separators).first ?? message
        let trimmed = firstSentence.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.count <= 30 { return trimmed }
        return String(trimmed.prefix(30)) + "..."
    }

    private func detectPriority(from message: String) -> VoxTask.TaskPriority {
        let urgentWords = ["紧急", "马上", "立刻", "urgent", "asap", "immediately"]
        let highWords = ["重要", "尽快", "important", "high priority"]

        let lower = message.lowercased()
        if urgentWords.contains(where: { lower.contains($0) }) { return .urgent }
        if highWords.contains(where: { lower.contains($0) }) { return .high }
        return .medium
    }
}
