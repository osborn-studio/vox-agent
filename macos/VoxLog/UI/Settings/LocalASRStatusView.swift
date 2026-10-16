import SwiftUI

/// Shared production status surface; callbacks are supplied by the settings owner.
struct LocalASRStatusView: View {
    let checking: Bool
    let available: Bool
    let legacy: Bool
    let failed: Bool
    let running: Bool
    let busy: Bool
    let startFailed: Bool
    let recheck: () -> Void
    let start: () -> Void
    let stop: () -> Void

    private var status: String {
        if checking { return L("正在校验本地模型…", "Verifying local model…") }
        if failed { return L("本地模型校验失败", "Local model verification failed") }
        if !available { return L("本地模型或运行组件尚未安装", "Local model or runtime is not installed") }
        if legacy { return L("检测到旧版模型，尚未进行完整性校验", "Legacy model detected; integrity not verified") }
        return L("本地模型完整性校验通过", "Local model integrity verified")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: TF.spacingSM) {
            Text("Qwen3-ASR-0.6B")
                .font(.headline)
                .foregroundStyle(TF.settingsText)
                .accessibilityAddTraits(.isHeader)
            Text(status)
                .foregroundStyle(TF.settingsText)
                .accessibilityIdentifier("localASR.validationStatus")
            if !checking && !available {
                Text(L("请导入已验证的模型并安装对应运行组件，然后重新检查。不会自动下载模型或切换云端识别。",
                       "Import a verified model and install its runtime, then recheck. No automatic download or switch to cloud ASR."))
                    .font(.caption)
                    .foregroundStyle(TF.settingsTextSecondary)
            }
            HStack(spacing: TF.spacingSM) {
                Text(busy ? L("正在更改服务状态", "Changing service state") :
                     (running ? L("本地识别运行中", "Local ASR running") : L("本地识别未运行", "Local ASR stopped")))
                    .foregroundStyle(TF.settingsText)
                if running {
                    Button(L("停止本地识别", "Stop local ASR"), action: stop)
                        .disabled(busy)
                        .accessibilityIdentifier("localASR.stop")
                }
                #if arch(arm64)
                if !running && available && !checking {
                    Button(L("启动本地识别", "Start local ASR"), action: start)
                        .disabled(busy)
                        .accessibilityIdentifier("localASR.start")
                }
                #endif
            }
            if startFailed {
                Label(L("本地识别启动失败，请检查模型和运行组件后重试。",
                        "Local ASR failed to start. Check the model and runtime, then retry."),
                      systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(TF.settingsText)
            }
            Button(L("重新检查模型", "Recheck model"), action: recheck)
                .disabled(checking)
                .accessibilityIdentifier("localASR.recheck")
                .accessibilityHint(L("检查本地文件，不下载模型", "Checks local files without downloading models"))
            Link(L("模型导入说明", "Model import instructions"),
                 destination: URL(string: "https://github.com/osborn-studio/osvox/blob/codex/context-model-updates/docs/verification/component-updates/offline-import.md")!)
        }
        .padding(.vertical, TF.spacingSM)
        .accessibilityElement(children: .contain)
    }
}
