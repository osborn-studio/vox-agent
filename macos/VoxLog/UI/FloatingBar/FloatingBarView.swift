import SwiftUI

enum FloatingHUDMetrics {
    static let width: CGFloat = TF.barWidth
    static let height: CGFloat = 84
    static let panelInset: CGFloat = 16
}

// MARK: - FloatingBarState Protocol

@MainActor
protocol FloatingBarState: AnyObject, Observable {
    var barPhase: FloatingBarPhase { get }
    var segments: [TranscriptionSegment] { get }
    var audioLevel: AudioLevelMeter { get }
    var currentMode: ProcessingMode { get }
    var feedbackMessage: String { get }
    var processingFinishTime: Date? { get }
    var transcriptionText: String { get }
    var recordingStartDate: Date? { get }
    var voiceTargetAgentName: String { get }
    var isQwen3OnlyMode: Bool { get }
}

/// Always-on-top voice HUD for global Vox Agent capture.
/// The stable rectangle, explicit target and real microphone meter distinguish this
/// from the legacy VoxLog capsule and keep capture visible outside the main window.
struct FloatingBarView<S: FloatingBarState>: View {
    let state: S

    @Environment(\.accessibilityReduceMotion) private var accessibilityReduceMotion
    @State private var borderBreathing = false

    var body: some View {
        Group {
            if state.barPhase != .hidden {
                hud
                    .transition(accessibilityReduceMotion ? .opacity : .scale(scale: 0.97).combined(with: .opacity))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .animation(accessibilityReduceMotion ? nil : TF.easeQuick, value: state.barPhase != .hidden)
        .onAppear(perform: updateBorderAnimation)
        .onChange(of: state.barPhase) { _, _ in updateBorderAnimation() }
        .onChange(of: accessibilityReduceMotion) { _, _ in updateBorderAnimation() }
    }

    private var hud: some View {
        VStack(alignment: .leading, spacing: 5) {
            header
            transcriptRow
            footer
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(width: FloatingHUDMetrics.width, height: FloatingHUDMetrics.height, alignment: .leading)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: TF.cornerLG, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: TF.cornerLG, style: .continuous)
                .strokeBorder(stateColor.opacity(borderOpacity), lineWidth: state.barPhase == .recording ? 1.5 : 1)
        }
        .shadow(color: .black.opacity(0.35), radius: 18, y: 8)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibilitySummary)
    }

    private var header: some View {
        HStack(spacing: TF.spacingSM) {
            statusMark
            Text(statusTitle)
                .font(.system(size: 10, weight: .semibold))
                .tracking(0.7)
                .foregroundStyle(.white.opacity(0.92))
                .lineLimit(1)

            Spacer(minLength: TF.spacingSM)

            Text("@\(state.voiceTargetAgentName)")
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .foregroundStyle(TF.amber)
                .lineLimit(1)
                .truncationMode(.middle)
                .accessibilityLabel(L("目标 Agent \(state.voiceTargetAgentName)", "Target agent \(state.voiceTargetAgentName)"))
        }
    }

    private var transcriptRow: some View {
        HStack(spacing: 10) {
            FloatingHUDWaveform(
                level: state.audioLevel,
                isActive: state.barPhase == .recording,
                tint: state.barPhase == .recording ? TF.recording : TF.idle
            )
            .frame(width: 50, height: 20)

            Text(transcriptText)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.white.opacity(transcriptOpacity))
                .lineLimit(1)
                .truncationMode(.head)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var footer: some View {
        HStack(spacing: TF.spacingSM) {
            Text(footerHint)
                .font(.system(size: 8.5, weight: .medium, design: .monospaced))
                .tracking(0.45)
                .foregroundStyle(.white.opacity(0.46))
                .lineLimit(1)

            Spacer(minLength: TF.spacingSM)

            Text(state.currentMode.name.uppercased())
                .font(.system(size: 8.5, weight: .semibold, design: .monospaced))
                .foregroundStyle(TF.accent.opacity(0.9))
                .lineLimit(1)
        }
    }

    @ViewBuilder
    private var statusMark: some View {
        switch state.barPhase {
        case .preparing, .recording:
            Circle()
                .fill(state.barPhase == .recording ? TF.recording : TF.amber)
                .frame(width: 7, height: 7)
        case .processing:
            Image(systemName: "bolt.horizontal.circle.fill")
                .foregroundStyle(TF.accent)
                .font(.system(size: 11, weight: .semibold))
        case .done:
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(TF.success)
                .font(.system(size: 11, weight: .semibold))
        case .error:
            Image(systemName: "exclamationmark.circle.fill")
                .foregroundStyle(TF.danger)
                .font(.system(size: 11, weight: .semibold))
        case .hidden:
            EmptyView()
        }
    }

    private var statusTitle: String {
        switch state.barPhase {
        case .preparing: return L("正在连接麦克风", "CONNECTING MICROPHONE")
        case .recording: return L("正在聆听 · 松开发送", "LISTENING · RELEASE TO SEND")
        case .processing: return L("正在交给 Agent", "PROCESSING WITH AGENT")
        case .done: return L("已发送给 Agent", "SENT TO AGENT")
        case .error: return L("语音任务失败", "VOICE TASK FAILED")
        case .hidden: return ""
        }
    }

    private var transcriptText: String {
        if !state.transcriptionText.isEmpty { return state.transcriptionText }
        switch state.barPhase {
        case .preparing: return L("准备安全的语音通道…", "Preparing a visible voice channel…")
        case .recording: return L("说出要交给 Agent 的任务…", "Speak the task you want the agent to handle…")
        case .processing: return L("正在整理语音指令…", "Structuring your voice instruction…")
        case .done, .error: return state.feedbackMessage
        case .hidden: return ""
        }
    }

    private var footerHint: String {
        switch state.barPhase {
        case .preparing, .recording:
            return L("ESC 取消 · TAB 切换目标", "ESC CANCEL · TAB SWITCH TARGET")
        case .processing:
            return L("ESC 跳过系统输入", "ESC SKIP SYSTEM INPUT")
        case .done:
            return L("任务已进入时间线", "TASK ADDED TO TIMELINE")
        case .error:
            return L("打开 Vox Agent 查看详情", "OPEN VOX AGENT FOR DETAILS")
        case .hidden:
            return ""
        }
    }

    private var stateColor: Color {
        switch state.barPhase {
        case .preparing: return TF.amber
        case .recording: return TF.recording
        case .processing: return TF.accent
        case .done: return TF.success
        case .error: return TF.danger
        case .hidden: return .clear
        }
    }

    private var borderOpacity: Double {
        guard state.barPhase == .recording, !accessibilityReduceMotion else { return 0.55 }
        return borderBreathing ? 0.92 : 0.38
    }

    private var transcriptOpacity: Double {
        state.transcriptionText.isEmpty ? 0.52 : 0.9
    }

    private var accessibilitySummary: String {
        "\(statusTitle). \(L("目标", "Target")): \(state.voiceTargetAgentName). \(transcriptText). \(footerHint)"
    }

    private func updateBorderAnimation() {
        borderBreathing = false
        guard state.barPhase == .recording, !accessibilityReduceMotion else { return }
        withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true)) {
            borderBreathing = true
        }
    }
}

private struct FloatingHUDWaveform: View {
    let level: AudioLevelMeter
    let isActive: Bool
    let tint: Color

    @Environment(\.accessibilityReduceMotion) private var accessibilityReduceMotion

    private let weights: [CGFloat] = [0.40, 0.68, 0.88, 1.0, 0.78, 0.58, 0.34]

    var body: some View {
        TimelineView(.periodic(
            from: .now,
            by: accessibilityReduceMotion ? 0.20 : 1.0 / 24.0
        )) { _ in
            HStack(alignment: .center, spacing: 3) {
                ForEach(Array(weights.enumerated()), id: \.offset) { _, weight in
                    RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                        .fill(tint)
                        .frame(width: 3, height: barHeight(weight: weight))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        }
        .accessibilityHidden(true)
    }

    private func barHeight(weight: CGFloat) -> CGFloat {
        guard isActive else { return 3 }
        let normalized = min(1, max(0, CGFloat(level.current)))
        let reducedMotionLevel = accessibilityReduceMotion ? normalized.rounded(toPlaces: 1) : normalized
        return max(3, 3 + reducedMotionLevel * 17 * weight)
    }
}

private extension CGFloat {
    func rounded(toPlaces places: Int) -> CGFloat {
        let divisor = pow(10.0, CGFloat(places))
        return (self * divisor).rounded() / divisor
    }
}
