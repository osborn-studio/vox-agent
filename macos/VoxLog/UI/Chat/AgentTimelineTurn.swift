import SwiftUI

/// One Agent-authored event in the ordered conversation timeline.
/// Text is intentionally unboxed; tool activity collapses to one mono row.
struct AgentTimelineTurn: View {
    let message: AgentChatMessage
    var agentEmoji: String = "🤖"
    var agentName: String = "Agent"

    var translationContent: AnyView? = nil
    var translationActions: AnyView? = nil

    @State private var isToolOutputExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: TF.spacingSM) {
            if message.type != .status {
                HStack(spacing: TF.spacingSM) {
                    Text(agentEmoji)
                        .font(.system(size: 13))
                    Text(agentName.uppercased())
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .foregroundStyle(.secondary)
                    if !message.taskId.isEmpty {
                        Text(String(message.taskId.prefix(8)))
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundStyle(.tertiary)
                    }
                    Spacer(minLength: TF.spacingSM)
                    if message.type == .text { translationActions }
                    Text(message.timestamp, style: .time)
                        .font(.system(size: ChatLayoutMetrics.metadataFontSize, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
            }

            switch message.type {
            case .text:
                textTurn
                translationContent
            case .toolUse:
                toolUseTurn
            case .toolResult:
                toolResultTurn
            case .status:
                statusTurn
            case .error:
                errorTurn
            }
        }
        .padding(.horizontal, TF.spacingMD)
        .padding(.vertical, TF.spacingXS)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var textTurn: some View {
        Text(message.content)
            .font(.system(size: ChatLayoutMetrics.messageFontSize))
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var toolUseTurn: some View {
        HStack(spacing: TF.spacingSM) {
            ProgressView()
                .controlSize(.small)
            Text("RUNNING · \(message.tool ?? "tool")")
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundStyle(.secondary)
            if let content = message.content.nilIfEmpty {
                Text(content)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var toolResultTurn: some View {
        VStack(alignment: .leading, spacing: TF.spacingSM) {
            Button {
                isToolOutputExpanded.toggle()
            } label: {
                HStack(spacing: TF.spacingSM) {
                    Image(systemName: isToolOutputExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                    Image(systemName: "checkmark.circle")
                        .foregroundStyle(TF.success)
                    Text("RESULT · \(message.tool ?? "tool")")
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L(
                "\(message.tool ?? "工具") 结果，\(isToolOutputExpanded ? "已展开" : "已折叠")",
                "\(message.tool ?? "Tool") result, \(isToolOutputExpanded ? "expanded" : "collapsed")"
            ))

            if isToolOutputExpanded, let output = message.toolOutput?.nilIfEmpty {
                ScrollView {
                    Text(output)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 200)
                .padding(.leading, TF.spacingMD)
                .overlay(alignment: .leading) {
                    Rectangle()
                        .fill(TF.line)
                        .frame(width: 1)
                }
            }
        }
    }

    private var statusTurn: some View {
        HStack(spacing: TF.spacingSM) {
            Image(systemName: "checkmark.circle")
                .foregroundStyle(TF.success)
            Text(message.content)
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundStyle(.secondary)
            Spacer(minLength: TF.spacingSM)
            Text(message.timestamp, style: .time)
                .font(.system(size: ChatLayoutMetrics.metadataFontSize, design: .monospaced))
                .foregroundStyle(.tertiary)
        }
        .accessibilityElement(children: .combine)
    }

    private var errorTurn: some View {
        HStack(alignment: .top, spacing: TF.spacingMD) {
            Rectangle()
                .fill(TF.danger)
                .frame(width: 2)
                .accessibilityHidden(true)
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(TF.danger)
            Text(message.content)
                .font(.system(size: ChatLayoutMetrics.messageFontSize))
                .foregroundStyle(TF.danger)
                .textSelection(.enabled)
        }
        .accessibilityLabel(L("Agent 失败：\(message.content)", "Agent failed: \(message.content)"))
    }
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}

/// Active work rendered as a compact timeline event, not a chat message.
struct TaskProgressBar: View {
    let progress: TaskProgressInfo

    var body: some View {
        HStack(spacing: TF.spacingMD) {
            Rectangle()
                .fill(TF.accent)
                .frame(width: 2, height: 24)
                .accessibilityHidden(true)
            ProgressView(value: progress.fraction)
                .frame(width: 80)
            Text(progress.label)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .padding(.horizontal, TF.spacingMD)
        .padding(.vertical, TF.spacingSM)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(L("任务进度：\(progress.label)", "Task progress: \(progress.label)"))
    }
}
