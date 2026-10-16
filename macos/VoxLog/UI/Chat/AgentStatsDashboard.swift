import SwiftUI

/// Dashboard showing agent performance stats and task metrics.
/// Displayed in the chat area when no agent is selected or via a toolbar button.
struct AgentStatsDashboard: View {

    @Environment(EngineCoordinator.self) private var engine: EngineCoordinator?
    @State private var stats: EngineStats?
    @State private var isLoading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                // Header
                HStack {
                    Text("Agent Dashboard")
                        .font(.system(size: 18, weight: .bold))
                    Spacer()
                    if isLoading {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Button("Refresh") {
                        Task { await loadStats() }
                    }
                    .font(.system(size: 11))
                }

                if let stats = stats {
                    // Overview cards
                    HStack(spacing: 12) {
                        StatCard(title: "Total Tasks", value: "\(stats.totalTasks)", color: .blue)
                        StatCard(title: "Completed", value: "\(stats.completed)", color: .green)
                        StatCard(title: "Failed", value: "\(stats.failed)", color: .red)
                        StatCard(title: "Active", value: "\(stats.active)", color: .orange)
                    }

                    // Today
                    HStack(spacing: 12) {
                        StatCard(title: "Today", value: "\(stats.tasksToday)", color: .purple)
                        StatCard(title: "This Week", value: "\(stats.tasksThisWeek)", color: .teal)
                    }

                    // Per-agent table
                    if !stats.agents.isEmpty {
                        Text("Per Agent")
                            .font(.system(size: 14, weight: .semibold))
                            .padding(.top, 8)

                        ForEach(stats.agents, id: \.agentId) { agent in
                            AgentStatRow(agent: agent)
                        }
                    }
                } else {
                    ContentUnavailableView(
                        "No Data",
                        systemImage: "chart.bar",
                        description: Text("Start the engine to see stats")
                    )
                }
            }
            .padding(20)
        }
        .task { await loadStats() }
    }

    private func loadStats() async {
        guard let port = engine?.port, port > 0 else { return }
        isLoading = true
        defer { isLoading = false }

        guard let url = URL(string: "http://127.0.0.1:\(port)/v1/analytics"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let decoded = try? JSONDecoder().decode(EngineStats.self, from: data)
        else { return }

        stats = decoded
    }
}

// MARK: - Subviews

struct StatCard: View {
    let title: String
    let value: String
    let color: Color

    var body: some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.system(size: 24, weight: .bold, design: .rounded))
                .foregroundStyle(color)
            Text(title)
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(color.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

struct AgentStatRow: View {
    let agent: AgentStat

    var body: some View {
        HStack {
            Text(agent.agentId)
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .frame(width: 120, alignment: .leading)

            Text("\(agent.totalTasks) tasks")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .frame(width: 60, alignment: .trailing)

            // Success rate bar
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(TF.line)
                    RoundedRectangle(cornerRadius: 3)
                        .fill(agent.successRate > 0.8 ? TF.success : agent.successRate > 0.5 ? Color.orange : Color.red)
                        .frame(width: geo.size.width * agent.successRate)
                }
            }
            .frame(height: 8)

            Text("\(Int(agent.successRate * 100))%")
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(.secondary)
                .frame(width: 35, alignment: .trailing)

            if agent.avgDuration > 0 {
                Text(String(format: "%.1fs", agent.avgDuration))
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.tertiary)
                    .frame(width: 45, alignment: .trailing)
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Models

struct EngineStats: Codable {
    let totalTasks: Int
    let completed: Int
    let failed: Int
    let cancelled: Int
    let active: Int
    let tasksToday: Int
    let tasksThisWeek: Int
    let agents: [AgentStat]

    enum CodingKeys: String, CodingKey {
        case totalTasks = "total_tasks"
        case completed, failed, cancelled, active
        case tasksToday = "tasks_today"
        case tasksThisWeek = "tasks_this_week"
        case agents
    }
}

struct AgentStat: Codable {
    let agentId: String
    let totalTasks: Int
    let completed: Int
    let failed: Int
    let successRate: Double
    let avgDuration: Double

    enum CodingKeys: String, CodingKey {
        case agentId = "agent_id"
        case totalTasks = "total_tasks"
        case completed, failed
        case successRate = "success_rate"
        case avgDuration = "avg_duration_sec"
    }
}
