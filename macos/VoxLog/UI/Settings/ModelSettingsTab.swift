import SwiftUI

struct ModelSettingsTab: View, SettingsCardHelpers {

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SettingsSectionHeader(
                label: "MODELS",
                title: L("模型配置", "Model Configuration"),
                description: L("语音识别与文本处理引擎配置。", "ASR and LLM engine configuration.")
            )

            ASRSettingsCard()

            Spacer().frame(height: 16)

            LLMSettingsCard()
        }
    }
}
