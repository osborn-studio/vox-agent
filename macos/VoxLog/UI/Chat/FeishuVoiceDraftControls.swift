import SwiftUI

/// The shared RecognitionSession owns capture. This view can only request a
/// frozen, account-scoped draft; it has no sending or history dependency.
struct FeishuVoiceDraftControls: View {
    @Bindable var appState: AppState

    var body: some View {
        HStack(spacing: TF.spacingSM) {
            if let id = appState.feishuOutbox.voiceDraftID {
                Label(appState.barPhase == .preparing
                    ? L("正在准备录音", "Preparing microphone")
                    : appState.barPhase == .recording
                        ? L("录音中 · 目标已固定", "Recording · destination locked")
                        : L("正在转写为草稿", "Transcribing draft"), systemImage: "mic.fill")
                    .foregroundStyle(TF.amber)
                Button(L("停止并生成草稿", "Stop and draft")) {
                    guard let session = AppDelegate.shared?.session else { return }
                    appState.stopRecording()
                    Task { await session.stopFeishuDraft(id) }
                }.disabled(appState.barPhase != .recording)
                Button(L("取消", "Cancel")) {
                    guard let session = AppDelegate.shared?.session else { return }
                    appState.cancel()
                    Task { await session.cancelFeishuDraft(id) }
                }
            } else {
                Button {
                    guard let session = AppDelegate.shared?.session,
                          let id = appState.feishuOutbox.beginVoiceDraft() else { return }
                    // Keep the global HUD visible if the user navigates elsewhere.
                    appState.activeFeishuVoiceID = id
                    appState.startRecording(showHUD: true)
                    Task { @MainActor in
                        guard appState.feishuOutbox.voiceDraftID == id else { return }
                        let started = await session.startFeishuDraft(id)
                        if appState.feishuOutbox.voiceDraftID != id {
                            await session.cancelFeishuDraft(id)
                        } else if !started && appState.barPhase == .preparing {
                            appState.showError(L("录音未启动，请检查模型和麦克风配置", "Recording did not start; check model and microphone settings"))
                        }
                    }
                } label: {
                    Label(L("语音草稿", "Voice draft"), systemImage: "mic")
                }
                .disabled(appState.barPhase == .recording || appState.barPhase == .preparing || appState.barPhase == .processing)
            }
            Text(L("仅转写，不自动润色或发送", "Transcription only; no automatic polish or sending"))
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
