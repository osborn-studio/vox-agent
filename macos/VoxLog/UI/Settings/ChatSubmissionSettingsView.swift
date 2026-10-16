import SwiftUI
import AppKit

struct ChatSubmissionSettingsView: View {
    @AppStorage(ChatSubmissionSettings.executionKey) private var execute = false
    @AppStorage(ChatSubmissionSettings.repositoryKey) private var repository = ""
    @State private var selectionError: String?

    var body: some View {
        GroupBox(L("聊天发送模式", "Chat Send Mode")) {
            VStack(alignment: .leading, spacing: TF.spacingSM) {
                Toggle(L("交给 Agent 执行", "Execute with Agent"), isOn: $execute)
                Text(execute
                     ? L("发送时调用当前 Agent 并显示回复，在下方仓库执行。", "Send to the selected agent and show its response in the repository below.")
                     : L("记录并同步：保存输入，按飞书备份设置同步，不调用 Agent。", "Record and sync: save input and follow Feishu backup settings without invoking an agent."))
                    .font(.caption).foregroundStyle(.secondary)
                if execute {
                    HStack {
                        Text(repository.isEmpty ? L("尚未选择代码仓库", "No repository selected") : repository)
                            .font(.caption).textSelection(.enabled)
                        Spacer()
                        Button(L("选择代码仓库…", "Choose Repository…")) { chooseRepository() }
                    }
                }
                Text(L("飞书同步需在下方单独启用。切换仅影响新发送的消息，不会取消已运行的任务。", "Enable Feishu backup below. Changes apply to new messages and do not cancel running tasks."))
                    .font(.caption).foregroundStyle(.secondary)
                if let selectionError { Text(selectionError).font(.caption).foregroundStyle(TF.danger) }
            }
            .padding(TF.spacingSM)
        }
    }

    private func chooseRepository() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        let previous = repository
        repository = url.path
        do {
            _ = try ChatSubmissionSettings.repository()
            selectionError = nil
        } catch {
            repository = previous
            selectionError = error.localizedDescription
        }
    }
}
