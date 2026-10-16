import SwiftUI

/// Settings tab for the Vox Agents Engine.
/// Shows engine status, registered tools, loaded skills, and memory.
struct EngineSettingsTab: View {

    @Environment(EngineCoordinator.self) private var engine: EngineCoordinator?

    @State private var tools: [ToolInfo] = []
    @State private var skills: [SkillInfo] = []
    @State private var memoryAgents: [String] = []
    @State private var isLoading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {

                // Engine Status
                GroupBox("引擎状态") {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Circle()
                                .fill(statusColor)
                                .frame(width: 8, height: 8)
                            Text(statusText)
                                .font(.system(size: 13))
                            Spacer()
                            if let port = engine?.port, port > 0 {
                                Text("Port: \(port)")
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundStyle(.secondary)
                            }
                        }

                        if let error = engine?.lastError {
                            Text(error)
                                .font(.system(size: 11))
                                .foregroundStyle(.red)
                        }

                        HStack(spacing: 12) {
                            Button(engine?.state == .running ? "停止引擎" : "启动引擎") {
                                if engine?.state == .running {
                                    engine?.stop()
                                } else {
                                    engine?.start()
                                }
                            }
                            .buttonStyle(.borderedProminent)

                            if engine?.state == .running {
                                Button("刷新") {
                                    Task { await loadEngineData() }
                                }
                            }
                        }
                    }
                    .padding(8)
                }

                // Tools
                if !tools.isEmpty {
                    GroupBox("已注册工具 (\(tools.count))") {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(tools, id: \.name) { tool in
                                HStack {
                                    Text("⚙️")
                                        .font(.system(size: 11))
                                    Text(tool.name)
                                        .font(.system(size: 12, weight: .medium, design: .monospaced))
                                    Text(tool.description)
                                        .font(.system(size: 11))
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                }
                            }
                        }
                        .padding(8)
                    }
                }

                // Skills
                GroupBox("已加载 Skills (\(skills.count))") {
                    if skills.isEmpty {
                        Text("无 Skills。将 .md 文件放入 ~/.voxlog/skills/ 目录。")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .padding(8)
                    } else {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(skills, id: \.name) { skill in
                                HStack {
                                    Text("📋")
                                    Text(skill.name)
                                        .font(.system(size: 12, weight: .medium))
                                    Text(skill.description)
                                        .font(.system(size: 11))
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                    Spacer()
                                    if !skill.tools.isEmpty {
                                        Text(skill.tools.joined(separator: ", "))
                                            .font(.system(size: 10, design: .monospaced))
                                            .foregroundStyle(.tertiary)
                                    }
                                }
                            }
                        }
                        .padding(8)
                    }
                }

                // Memory
                if !memoryAgents.isEmpty {
                    GroupBox("Agent 记忆 (\(memoryAgents.count) agents)") {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(memoryAgents, id: \.self) { agentId in
                                HStack {
                                    Text("🧠")
                                    Text(agentId)
                                        .font(.system(size: 12, design: .monospaced))
                                    Spacer()
                                    Button("清除") {
                                        Task { await clearMemory(agentId) }
                                    }
                                    .font(.system(size: 10))
                                    .buttonStyle(.plain)
                                    .foregroundStyle(.red)
                                }
                            }
                        }
                        .padding(8)
                    }
                }

                // API Docs link
                if let port = engine?.port, port > 0, engine?.state == .running {
                    GroupBox("开发者") {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text("API 文档:")
                                    .font(.system(size: 11))
                                Link("http://127.0.0.1:\(port)/docs",
                                     destination: URL(string: "http://127.0.0.1:\(port)/docs")!)
                                    .font(.system(size: 11, design: .monospaced))
                            }
                            HStack {
                                Text("WebSocket:")
                                    .font(.system(size: 11))
                                Text("ws://127.0.0.1:\(port)/ws")
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                            }
                        }
                        .padding(8)
                    }
                }
            }
            .padding(20)
        }
        .task {
            if engine?.state == .running {
                await loadEngineData()
            }
        }
    }

    // MARK: - Helpers

    private var statusColor: Color {
        switch engine?.state {
        case .running: .green
        case .starting: .yellow
        case .error: .red
        default: .gray
        }
    }

    private var statusText: String {
        switch engine?.state {
        case .running: "运行中"
        case .starting: "启动中..."
        case .error: "错误 (重启 \(engine?.restartCount ?? 0)/3)"
        case .stopped: "已停止"
        default: "未知"
        }
    }

    private func loadEngineData() async {
        guard let port = engine?.port, port > 0 else { return }
        let base = "http://127.0.0.1:\(port)"

        // Load tools
        if let url = URL(string: "\(base)/v1/tools"),
           let (data, _) = try? await URLSession.shared.data(from: url),
           let items = try? JSONDecoder().decode([ToolInfo].self, from: data) {
            tools = items
        }

        // Load skills
        if let url = URL(string: "\(base)/v1/skills"),
           let (data, _) = try? await URLSession.shared.data(from: url),
           let items = try? JSONDecoder().decode([SkillInfo].self, from: data) {
            skills = items
        }

        // Load memory agents
        if let url = URL(string: "\(base)/v1/memory"),
           let (data, _) = try? await URLSession.shared.data(from: url),
           let items = try? JSONDecoder().decode([String].self, from: data) {
            memoryAgents = items
        }
    }

    private func clearMemory(_ agentId: String) async {
        guard let port = engine?.port else { return }
        guard let url = URL(string: "http://127.0.0.1:\(port)/v1/memory/\(agentId)") else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        _ = try? await URLSession.shared.data(for: request)
        await loadEngineData()
    }
}

// MARK: - Data Models

struct ToolInfo: Codable {
    let name: String
    let description: String
}

struct SkillInfo: Codable {
    let name: String
    let description: String
    let tools: [String]
}
