import SwiftUI
import AppKit

struct ChatInputBar: View {

    @Environment(AppState.self) private var appState
    @Environment(\.accessibilityReduceMotion) private var accessibilityReduceMotion
    @Binding var textInput: String
    let selectedAgent: String
    var onSend: (String) -> Bool
    var onDraftStart: () -> Void = {}

    @State private var voiceState = VoiceInputStateMachine()
    @State private var voiceSamplerTask: Task<Void, Never>?
    @State private var voiceSessionTask: Task<Void, Never>?
    @State private var ownsVoiceSession = false
    @State private var voiceInputPrefix = ""
    @State private var rawTranscript = ""
    @State private var voiceStartedAt: Date?
    @State private var firstTranscriptLatency: TimeInterval?
    @State private var fixedWordCount = 0
    @State private var voiceError: String?
    @State private var hasPolished = false
    @State private var inputRenderID = UUID()
    @State private var inputGeneration = UUID()

    var body: some View {
        GeometryReader { geometry in
            composerBody(width: max(0, geometry.size.width - ChatLayoutMetrics.inputHorizontalInset * 2))
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 10)
        }
        .frame(height: composerHeight + 20)
        .frame(minWidth: 0, maxWidth: .infinity)
        .animation(accessibilityReduceMotion ? nil : TF.springSnappy, value: voiceState.stage)
        .onChange(of: appState.transcriptionText) {
            syncTranscriptFromAppState()
        }
        .onChange(of: appState.barPhase) {
            syncPhaseFromAppState()
        }
        .onDisappear {
            inputGeneration = UUID()
            if isCapturing || voiceState.stage == .processing {
                handleEscape()
            }
        }
    }

    private func composerBody(width: CGFloat) -> some View {
        ZStack(alignment: .bottomTrailing) {
            VStack(alignment: .leading, spacing: 0) {
                if isVoiceExpanded {
                    voiceHeader
                        .padding(.horizontal, TF.spacingMD)
                        .padding(.top, TF.spacingSM)
                }

                inputSurface
            }
            .frame(height: composerHeight)
            .frame(minWidth: 0, maxWidth: .infinity)
            .background(RoundedRectangle(cornerRadius: TF.cornerLG).fill(TF.surfaceRaised))
            .overlay(
                RoundedRectangle(cornerRadius: TF.cornerLG)
                    .stroke(isCapturing ? TF.amber.opacity(0.55) : TF.line, lineWidth: isCapturing ? 1.5 : 1)
            )

            HStack(spacing: 6) {
                micButton

                composerIconButton(
                    systemImage: "arrow.up",
                    fill: canSend ? TF.accent : Color.secondary.opacity(0.2),
                    foreground: canSend ? .white : .secondary,
                    help: L("发送", "Send"),
                    disabled: !canSend,
                    action: handleSend
                )
            }
            .fixedSize()
            .layoutPriority(10)
            .padding(.trailing, 10)
            .padding(.bottom, 10)
        }
        .frame(width: width)
    }

    private var inputSurface: some View {
        ZStack(alignment: .topLeading) {
            ChatInputTextView(
                text: $textInput,
                fontSize: voiceState.stage == .finalized ? 13 : ChatLayoutMetrics.inputFontSize,
                isEditable: voiceState.stage == .idle || voiceState.stage == .finalized,
                textColor: voiceState.stage == .processing ? .tertiaryLabelColor : .labelColor,
                onSubmit: handleSend,
                onEscape: handleEscape
            )
            .id(inputRenderID)
            .opacity(voiceState.stage == .processing ? 0.45 : 1)
            .padding(.trailing, ChatLayoutMetrics.inputButtonReserveWidth)

            if textInput.isEmpty {
                Text(inputPlaceholder)
                    .font(.system(size: ChatLayoutMetrics.inputFontSize))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .padding(.trailing, ChatLayoutMetrics.inputButtonReserveWidth)
                    .allowsHitTesting(false)
            }


        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var voiceHeader: some View {
        HStack(spacing: TF.spacingMD) {
            VoiceWaveform(
                levelMeter: appState.audioLevel,
                isActive: isCapturing,
                reduceMotion: accessibilityReduceMotion
            )
            .frame(width: 72, height: ChatLayoutMetrics.voiceWaveformHeight)

            VStack(alignment: .leading, spacing: 2) {
                Text(voiceStageTitle)
                    .font(.system(size: 11, weight: .semibold))
                Text(voiceStageDetail)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }

            Spacer(minLength: TF.spacingSM)

            if let voiceError {
                Label(voiceError, systemImage: "exclamationmark.circle")
                    .font(.system(size: 10))
                    .foregroundStyle(TF.danger)
                    .lineLimit(2)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(L(
            "语音输入：\(voiceStageTitle)，\(voiceStageDetail)",
            "Voice input: \(voiceStageTitle), \(voiceStageDetail)"
        ))
    }

    private var micButton: some View {
        composerIconButton(
            systemImage: micSystemImage,
            fill: micFill,
            foreground: isCapturing ? .white : .primary,
            help: micHelp,
            disabled: voiceState.stage == .processing,
            action: micTapped
        )
        .overlay {
            if voiceState.stage == .counting {
                VoiceCountdownRing(progress: voiceState.countdownProgress)
                    .frame(width: 36, height: 36)
                    .accessibilityHidden(true)
            }
        }
    }

    private func composerIconButton(
        systemImage: String,
        fill: Color,
        foreground: Color,
        help: String,
        disabled: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(foreground)
                .frame(width: 30, height: 30)
                .background(Circle().fill(fill))
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .help(help)
    }

    private var hasText: Bool {
        !textInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var canSend: Bool {
        hasText && (voiceState.stage == .idle || voiceState.stage == .finalized)
    }

    private var isCapturing: Bool {
        voiceState.stage == .listening || voiceState.stage == .counting
    }

    private var isVoiceExpanded: Bool {
        voiceState.stage != .idle || voiceError != nil
    }

    private var composerHeight: CGFloat {
        isVoiceExpanded
            ? ChatLayoutMetrics.voiceInputExpandedHeight
            : ChatLayoutMetrics.inputRestHeight
    }

    private func micTapped() {
        switch voiceState.stage {
        case .listening, .counting:
            stopRecording()
        case .idle, .finalized:
            startRecording()
        case .processing:
            break
        }
    }

    private func startRecording() {
        guard appState.activeFeishuVoiceID == nil else { return }
        guard let delegate = AppDelegate.shared else { return }
        onDraftStart()
        voiceInputPrefix = textInput.trimmingCharacters(in: .whitespacesAndNewlines)
        rawTranscript = ""
        voiceStartedAt = Date()
        firstTranscriptLatency = nil
        fixedWordCount = 0
        voiceError = nil
        hasPolished = false
        ownsVoiceSession = true
        inputGeneration = UUID()
        let generation = inputGeneration
        voiceState.start()

        // The expanded composer is already the visible voice surface; the global
        // HUD is reserved for capture while another app owns focus.
        appState.startRecording(showHUD: false)
        startVoiceSampler()
        voiceSessionTask?.cancel()
        voiceSessionTask = Task { @MainActor in
            await delegate.session.setAgent(selectedAgent)
            guard !Task.isCancelled,
                  inputGeneration == generation,
                  isCapturing else { return }
            await delegate.session.startRecording(
                mode: appState.currentMode,
                outputDestination: .composer
            )
            if Task.isCancelled || inputGeneration != generation {
                await delegate.session.cancelRecording()
            }
            voiceSessionTask = nil
        }
    }

    private func stopRecording(autoTriggered: Bool = false) {
        guard let delegate = AppDelegate.shared else { return }
        guard isCapturing || (autoTriggered && voiceState.stage == .processing) else { return }
        voiceState.beginProcessing()
        voiceSamplerTask?.cancel()
        voiceSamplerTask = nil
        voiceSessionTask?.cancel()
        appState.stopRecording()

        Task { @MainActor in
            switch await delegate.session.state {
            case .recording:
                await delegate.session.stopRecording()
            case .starting:
                await delegate.session.cancelRecording()
                voiceState.reset()
                ownsVoiceSession = false
            case .idle:
                voiceState.reset()
                ownsVoiceSession = false
                appState.cancel()
            case .finishing, .postProcessing, .injecting:
                break
            }
        }
    }

    private func handleSend() {
        switch voiceState.stage {
        case .listening, .counting:
            stopRecording()
            return
        case .processing:
            return
        case .idle, .finalized:
            break
        }

        guard hasText else { return }
        if voiceState.stage == .finalized || hasPolished {
            sendCurrentText()
            return
        }

        let generation = inputGeneration
        Task { @MainActor in
            guard inputGeneration == generation else { return }
            let corrected = SnippetStorage.applyEffective(to: textInput)
            if corrected != textInput {
                textInput = corrected
            }
            guard await polishInPlace() else { return }
            guard !Task.isCancelled, inputGeneration == generation else { return }
            sendCurrentText()
        }
    }

    private func sendCurrentText() {
        let text = textInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        guard onSend(text) else { return }
        resetInputAfterSend()
    }

    private func resetInputAfterSend() {
        textInput = ""
        voiceInputPrefix = ""
        rawTranscript = ""
        fixedWordCount = 0
        voiceError = nil
        hasPolished = false
        inputRenderID = UUID()
        inputGeneration = UUID()
        voiceState.reset()
        ownsVoiceSession = false
        voiceSessionTask?.cancel()
        voiceSessionTask = nil
        appState.cancel()
    }

    private func handleEscape() {
        guard let delegate = AppDelegate.shared else { return }
        switch voiceState.stage {
        case .listening, .counting, .processing:
            voiceSamplerTask?.cancel()
            voiceSamplerTask = nil
            voiceSessionTask?.cancel()
            voiceSessionTask = nil
            inputGeneration = UUID()
            voiceState.reset()
            ownsVoiceSession = false
            appState.cancel()
            Task { await delegate.session.cancelRecording() }
        case .finalized:
            textInput = voiceInputPrefix
            rawTranscript = ""
            voiceState.reset()
            inputRenderID = UUID()
        case .idle:
            break
        }
    }

    private func startVoiceSampler() {
        voiceSamplerTask?.cancel()
        voiceSamplerTask = Task { @MainActor in
            while !Task.isCancelled && isCapturing {
                let shouldStop = voiceState.sample(
                    level: appState.audioLevel.current,
                    at: Date()
                )
                if shouldStop {
                    stopRecording(autoTriggered: true)
                    return
                }
                try? await Task.sleep(for: .milliseconds(50))
            }
        }
    }

    private func syncTranscriptFromAppState() {
        guard ownsVoiceSession else { return }
        let transcript = appState.transcriptionText
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !transcript.isEmpty else { return }

        switch voiceState.stage {
        case .listening, .counting:
            rawTranscript = transcript
            if firstTranscriptLatency == nil, let voiceStartedAt {
                firstTranscriptLatency = Date().timeIntervalSince(voiceStartedAt)
            }
            textInput = composedInput(prefix: voiceInputPrefix, voiceText: transcript)
        case .finalized:
            applyFinalTranscript(transcript)
        case .idle, .processing:
            break
        }
    }

    private func syncPhaseFromAppState() {
        guard ownsVoiceSession else { return }
        switch appState.barPhase {
        case .preparing, .recording:
            break
        case .processing:
            voiceState.beginProcessing()
            voiceSamplerTask?.cancel()
            voiceSamplerTask = nil
        case .done:
            let finalText = appState.transcriptionText
                .trimmingCharacters(in: .whitespacesAndNewlines)
            guard !finalText.isEmpty else {
                voiceState.reset()
                ownsVoiceSession = false
                return
            }
            applyFinalTranscript(finalText)
            voiceState.finalize()
            hasPolished = true
            ownsVoiceSession = false
        case .error:
            voiceError = appState.feedbackMessage
            voiceState.reset()
            ownsVoiceSession = false
            voiceSamplerTask?.cancel()
            voiceSamplerTask = nil
        case .hidden:
            if voiceState.stage == .processing && appState.transcriptionText.isEmpty {
                voiceState.reset()
                ownsVoiceSession = false
            }
        }
    }

    private func applyFinalTranscript(_ finalText: String) {
        fixedWordCount = rawTranscript.isEmpty
            ? 0
            : VoiceTranscriptDiff.changedWordCount(raw: rawTranscript, final: finalText)
        let update = {
            textInput = composedInput(prefix: voiceInputPrefix, voiceText: finalText)
            inputRenderID = UUID()
        }
        if accessibilityReduceMotion {
            update()
        } else {
            withAnimation(.easeOut(duration: 0.15), update)
        }
    }

    private func composedInput(prefix: String, voiceText: String) -> String {
        guard !prefix.isEmpty else { return voiceText }
        guard !voiceText.isEmpty else { return prefix }
        return prefix + (prefix.last?.isWhitespace == true ? "" : " ") + voiceText
    }

    private func polishInPlace() async -> Bool {
        let text = await MainActor.run { textInput }
        let generation = await MainActor.run { inputGeneration }
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return false }
        guard let cfg = KeychainService.loadSelectedLLMConfig() else { return true }

        let llmCfg = cfg.toLLMConfig()
        let client = DoubaoChatClient(provider: KeychainService.selectedLLMProvider)
        let prompt: String
        if let m = await MainActor.run(body: { appState.availableModes.first(where: { !$0.prompt.isEmpty }) }) {
            prompt = m.prompt.replacingOccurrences(of: "{text}", with: text)
        } else {
            prompt = L(
                "去掉口头禅，修正标点，保持原意，直接返回：\n\(text)",
                "Remove filler words, fix punctuation, preserve meaning, and return only the result:\n\(text)"
            )
        }

        do {
            let result = try await client.process(text: text, prompt: prompt, config: llmCfg)
            let cleaned = result.trimmingCharacters(in: .whitespacesAndNewlines)
            return await MainActor.run {
                guard !Task.isCancelled, inputGeneration == generation, textInput == text else { return false }
                if !cleaned.isEmpty && cleaned != text {
                    textInput = cleaned
                    hasPolished = true
                }
                return true
            }
        } catch {
            return await MainActor.run { !Task.isCancelled && inputGeneration == generation && textInput == text }
        }
    }

    private var inputPlaceholder: String {
        switch voiceState.stage {
        case .listening, .counting:
            return L("开始说话…", "Speak now…")
        case .processing:
            return L("正在整理转写…", "Polishing transcript…")
        case .idle, .finalized:
            return L("输入文字…", "Type a message…")
        }
    }

    private var voiceStageTitle: String {
        switch voiceState.stage {
        case .idle: return L("语音就绪", "Voice ready")
        case .listening:
            return appState.barPhase == .preparing
                ? L("正在连接", "Connecting")
                : L("正在聆听", "Listening")
        case .counting: return L("检测到停顿", "Pause detected")
        case .processing: return L("正在整理", "Polishing")
        case .finalized: return L("转写已定稿", "Transcript finalized")
        }
    }

    private var voiceStageDetail: String {
        let provider = KeychainService.selectedASRProvider.displayName
        switch voiceState.stage {
        case .idle:
            return provider
        case .listening:
            return "● LISTENING · \(provider)"
        case .counting:
            let remaining = max(0, VoiceInputStateMachine.pauseDuration
                * (1 - voiceState.countdownProgress))
            return String(format: "PAUSE DETECTED · %.1fs", remaining)
        case .processing:
            return L("POLISHING · DICTIONARY", "POLISHING · DICTIONARY")
        case .finalized:
            let latency = firstTranscriptLatency.map { " · \(Int($0 * 1_000))ms" } ?? ""
            return L(
                "修正 \(fixedWordCount) 词 · ↩ 发送 · ESC 重录\(latency)",
                "\(fixedWordCount) WORDS FIXED · ↩ SEND · ESC RE-RECORD\(latency)"
            )
        }
    }

    private var micSystemImage: String {
        switch voiceState.stage {
        case .listening, .counting: return "stop.fill"
        case .processing: return "ellipsis"
        case .idle, .finalized: return "mic.fill"
        }
    }

    private var micFill: Color {
        isCapturing ? TF.recording : TF.surface
    }

    private var micHelp: String {
        switch voiceState.stage {
        case .listening, .counting: return L("停止录音", "Stop recording")
        case .processing: return L("正在处理", "Processing")
        case .idle, .finalized: return L("开始录音", "Start recording")
        }
    }
}

private struct VoiceWaveform: View {
    let levelMeter: AudioLevelMeter
    let isActive: Bool
    let reduceMotion: Bool

    var body: some View {
        Group {
            if reduceMotion {
                TimelineView(.periodic(from: .now, by: 0.2)) { _ in
                    bars(level: isActive ? levelMeter.current : 0)
                }
            } else {
                TimelineView(.animation(minimumInterval: 1.0 / 20.0, paused: !isActive)) { _ in
                    bars(level: isActive ? levelMeter.current : 0)
                }
            }
        }
        .accessibilityHidden(true)
    }

    private func bars(level: Float) -> some View {
        let normalized = max(0, min(1, CGFloat(level)))
        let multipliers: [CGFloat] = [0.45, 0.75, 1, 0.68, 0.92, 0.58, 0.38]
        return HStack(alignment: .center, spacing: 3) {
            ForEach(Array(multipliers.enumerated()), id: \.offset) { _, multiplier in
                Capsule()
                    .fill(isActive ? TF.amber : TF.idle)
                    .frame(width: 4, height: max(3, normalized * 28 * multiplier))
            }
        }
    }
}

private struct VoiceCountdownRing: View {
    let progress: Double

    var body: some View {
        Circle()
            .trim(from: 0, to: progress)
            .stroke(TF.amber, style: StrokeStyle(lineWidth: 2, lineCap: .round))
            .rotationEffect(.degrees(-90))
    }
}

private struct ChatInputTextView: NSViewRepresentable {
    @Binding var text: String
    let fontSize: CGFloat
    let isEditable: Bool
    let textColor: NSColor
    let onSubmit: () -> Void
    let onEscape: () -> Void

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = CompressibleTextScrollView()
        scrollView.drawsBackground = false
        scrollView.hasVerticalScroller = false
        scrollView.hasHorizontalScroller = false
        scrollView.autohidesScrollers = true
        scrollView.setContentHuggingPriority(.defaultLow, for: .horizontal)
        scrollView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

        let textView = SubmitAwareTextView()
        textView.delegate = context.coordinator
        textView.onSubmit = onSubmit
        textView.onEscape = onEscape
        textView.isRichText = false
        textView.isEditable = isEditable
        textView.isSelectable = true
        textView.allowsUndo = true
        textView.importsGraphics = false
        textView.drawsBackground = false
        textView.backgroundColor = .clear
        textView.font = .systemFont(ofSize: fontSize)
        textView.textColor = textColor
        textView.insertionPointColor = .controlAccentColor
        textView.textContainerInset = NSSize(width: 10, height: 8)
        textView.textContainer?.lineFragmentPadding = 0
        textView.textContainer?.widthTracksTextView = true
        textView.isHorizontallyResizable = false
        textView.isVerticallyResizable = true
        textView.minSize = NSSize(width: 0, height: 0)
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        textView.autoresizingMask = [.width]
        textView.setContentHuggingPriority(.defaultLow, for: .horizontal)
        textView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        textView.string = text

        scrollView.documentView = textView
        return scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        guard let textView = scrollView.documentView as? SubmitAwareTextView else { return }
        textView.onSubmit = onSubmit
        textView.onEscape = onEscape
        textView.font = .systemFont(ofSize: fontSize)
        textView.isEditable = isEditable
        textView.textColor = textColor
        if textView.string != text {
            textView.string = text
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(text: $text)
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        @Binding var text: String

        init(text: Binding<String>) {
            _text = text
        }

        func textDidChange(_ notification: Notification) {
            guard let textView = notification.object as? NSTextView else { return }
            text = textView.string
        }
    }
}

private final class CompressibleTextScrollView: NSScrollView {
    override var intrinsicContentSize: NSSize {
        NSSize(width: NSView.noIntrinsicMetric, height: NSView.noIntrinsicMetric)
    }
}

private final class SubmitAwareTextView: NSTextView {
    var onSubmit: (() -> Void)?
    var onEscape: (() -> Void)?

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 {
            onEscape?()
            return
        }
        let isReturn = event.keyCode == 36 || event.keyCode == 76
        if isReturn && !event.modifierFlags.contains(.shift) {
            onSubmit?()
            return
        }
        super.keyDown(with: event)
    }
}
