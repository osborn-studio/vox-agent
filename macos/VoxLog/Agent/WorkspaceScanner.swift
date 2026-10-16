import Foundation
import os

/// Scans the OSBORN Core workspace (~/.osborn/config/agents/) to discover agents.
/// VoxLog agents are a 1:1 mirror of the workspace — not manually created.
actor WorkspaceScanner {

    static let shared = WorkspaceScanner()
    private let logger = Logger(subsystem: "com.voxlog.workspace", category: "WorkspaceScanner")

    private let agentsDir = NSHomeDirectory() + "/.osborn/config/agents"

    /// Scan the workspace and return all active agents.
    func scanAgents() -> [Agent] {
        let fm = FileManager.default
        guard let dirs = try? fm.contentsOfDirectory(atPath: agentsDir) else {
            logger.warning("Cannot read \(self.agentsDir)")
            return Agent.defaults  // Fallback to hardcoded defaults
        }

        var agents: [Agent] = []

        for dirName in dirs.sorted() {
            let dirPath = "\(agentsDir)/\(dirName)"
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: dirPath, isDirectory: &isDir), isDir.boolValue else { continue }

            // Skip archive
            if dirName == "archive" { continue }

            let identityPath = "\(dirPath)/identity.md"
            guard fm.fileExists(atPath: identityPath) else { continue }

            // Parse identity.md frontmatter — only top-level agents, no sub-agents
            if let agent = parseIdentity(dirName: dirName, path: identityPath) {
                agents.append(agent)
            }
        }

        logger.info("Scanned \(agents.count) agents from workspace")
        return AgentRosterPolicy.curated(agents)
    }

    // MARK: - Parse identity.md

    private func parseIdentity(dirName: String, path: String) -> Agent? {
        guard let content = try? String(contentsOfFile: path, encoding: .utf8) else { return nil }

        // Try YAML frontmatter first (--- delimited)
        var fields = parseFrontmatter(content)

        // Fallback: parse Markdown format (- **Key:** Value)
        if fields.isEmpty || fields["name"] == nil {
            let mdFields = parseMarkdownFields(content)
            for (k, v) in mdFields where fields[k] == nil {
                fields[k] = v
            }
        }

        let id = fields["id"] ?? dirName
        let name = fields["name"] ?? dirName.replacingOccurrences(of: "_", with: " ").capitalized
        let emoji = fields["icon"] ?? fields["emoji"] ?? "🤖"
        let status = fields["status"] ?? "active"

        // Skip inactive agents
        if status != "active" && !status.isEmpty { return nil }

        // Skip if name is empty or just the directory name
        guard !name.isEmpty else { return nil }

        return Agent(
            id: id,
            name: name,
            emoji: emoji,
            agentType: .systemDefault,
            parentId: nil
        )
    }

    /// Parse Markdown-style fields: - **Key:** Value
    private func parseMarkdownFields(_ content: String) -> [String: String] {
        var result: [String: String] = [:]
        for line in content.components(separatedBy: .newlines) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            // Match: - **Name:** OSBORN
            if trimmed.hasPrefix("- **"),
               let endBold = trimmed.range(of: ":**") {
                let key = String(trimmed[trimmed.index(trimmed.startIndex, offsetBy: 4)..<endBold.lowerBound])
                    .lowercased()
                let value = String(trimmed[endBold.upperBound...])
                    .trimmingCharacters(in: .whitespaces)
                    .trimmingCharacters(in: CharacterSet(charactersIn: "`"))
                result[key] = value
            }
        }
        return result
    }

    // MARK: - Parse agents.md for sub-agents

    private func parseSubAgents(parentId: String, path: String) -> [Agent] {
        guard let content = try? String(contentsOfFile: path, encoding: .utf8) else { return [] }

        var subAgents: [Agent] = []

        // Look for lines like: ## agent_name or - **agent_name**: description
        for line in content.components(separatedBy: .newlines) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // Pattern: ## Sub Agent Name
            if trimmed.hasPrefix("## ") {
                let name = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                if !name.isEmpty && name != "Overview" && name != "概述" {
                    let id = "\(parentId)/\(name.lowercased().replacingOccurrences(of: " ", with: "-"))"
                    subAgents.append(Agent(
                        id: id,
                        name: name,
                        emoji: "▸",
                        agentType: .systemDefault,
                        parentId: parentId
                    ))
                }
            }
        }

        return subAgents
    }

    // MARK: - Frontmatter Parser

    private func parseFrontmatter(_ content: String) -> [String: String] {
        var result: [String: String] = [:]

        let lines = content.components(separatedBy: .newlines)
        guard lines.first?.trimmingCharacters(in: .whitespaces) == "---" else { return result }

        for i in 1..<lines.count {
            let line = lines[i].trimmingCharacters(in: .whitespaces)
            if line == "---" { break }

            if let colonIdx = line.firstIndex(of: ":") {
                let key = String(line[line.startIndex..<colonIdx]).trimmingCharacters(in: .whitespaces)
                var value = String(line[line.index(after: colonIdx)...]).trimmingCharacters(in: .whitespaces)
                // Remove quotes
                if value.hasPrefix("\"") && value.hasSuffix("\"") {
                    value = String(value.dropFirst().dropLast())
                }
                result[key] = value
            }
        }

        return result
    }
}
