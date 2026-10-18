import SwiftUI
import AppKit

/// Conversation composer. Layout follows a two-row pattern: the text area on
/// top grows upward with its content (Shift+Return adds lines) until
/// `ChatLayoutMetrics.inputMaxHeight`, then scrolls; the control row below
/// holds Translate on the left and the Polish model badge, mic and send on
/// the right. Capture is owned by the shared `RecognitionSession`.
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
    /// The current draft has already been through Polish (or translation) and
    /// is sent as is. Cleared whenever the draft is emptied or restored.
    @State private var hasPolished = false
    @State private var isPolishing = false
    /// Inline reason the last Polish attempt failed. The draft is kept; the
    /// user retries with Send or sends the original explicitly.
    @State private var polishFailure: String?
    @State private var isTranslating = false
    @State private var translationTask: Task<Void, Never>?
    /// Draft text before the last translation, for one-click restore.
    @State private var translationOriginal: String?
    @State private var translationNote: String?
    @State private var measuredTextHeight: CGFloat = ChatLayoutMetrics.inputTextMinHeight
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
        .onChange(of: textInput) {
            draftDidChange()
        }
        .onDisappear {
            inputGeneration = UUID()
            translationTask?.cancel()
            translationTask = nil
            if isCapturing || voiceState.stage == .processing {
                handleEscape()
            }
        }
    }

    // MARK: - Layout

    private func composerBody(width: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            if isVoiceExpanded {
                voiceHeader
                    .padding(.horizontal, TF.spacingMD)
                    .frame(height: ChatLayoutMetrics.voiceHeaderHeight)
            }

            inputSurface
                .frame(height: textAreaHeight)

            composerToolbar
        }
        .frame(height: composerHeight)
        .frame(minWidth: 0, maxWidth: .infinity)
        .background(RoundedRectangle(cornerRadius: TF.cornerLG).fill(TF.surfaceRaised))
        .overlay(
            RoundedRectangle(cornerRadius: TF.cornerLG)
                .stroke(isCapturing ? TF.amber.opacity(0.55) : TF.line, lineWidth: isCapturing ? 1.5 : 1)
        )
        .frame(width: width)
    }

    private var inputSurface: some View {
        ZStack(alignment: .topLeading) {
            ChatInputTextView(
                text: $textInput,
                fontSize: voiceState.stage == .finalized ? 13 : ChatLayoutMetrics.inputFontSize,
                isEditable: isEditable,
                textColor: voiceState.stage == .processing ? .tertiaryLabelColor : .labelColor,
                onSubmit: handleSend,
                onEscape: handleEscape,
                onContentHeightChange: { height in
                    if abs(measuredTextHeight - height) > 0.5 {
                        measuredTextHeight = height
                    }
                }
            )
            .id(inputRenderID)
            .opacity(voiceState.stage == .processing || isTranslating || isPolishing ? 0.45 : 1)

            if textInput.isEmpty {
                Text(inputPlaceholder)
                    .font(.system(size: ChatLayoutMetrics.inputFontSize))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .allowsHitTesting(false)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// Bottom row: Translate (left) · status note · model badge, mic, send (right).
    private var composerToolbar: some View {
        HStack(spacing: TF.spacingSM) {
            translateControls

            if let note = toolbarNote {
                Text(note)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }

            if polishFailure != nil {
                Button(L("直接发送原文", "Send as typed"), action: sendUnpolished)
                    .buttonStyle(.plain)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(TF.accent)
                    .help(L("跳过润色，按当前文字发送", "Skip Polish and send the current text"))
            }

            Spacer(minLength: TF.spacingSM)

            providerBadge

            HStack(spacing: 6) {
                micButton

                composerIconButton(
                    systemImage: "arrow.up",
                    fill: canSend ? TF.accent : Color.secondary.opacity(0.2),
                    foreground: canSend ? .white : .secondary,
                    help: sendHelp,
                    disabled: !canSend,
                    action: handleSend
                )
            }
            .fixedSize()
            .layoutPriority(10)
        }
        .frame(height: ChatLayoutMetrics.inputToolbarHeight)
        .padding(.leading, TF.spacingMD)
        .padding(.trailing, 10)
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

    // MARK: - Translate

    @ViewBuilder private var translateControls: some View {
        Button(action: translateDraft) {
            Label(
                isTranslating ? L("翻译中…", "Translating…") : L("翻译", "Translate"),
                systemImage: "character.bubble"
            )
            .labelStyle(.titleAndIcon)
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(canTranslate || isTranslating ? Color.primary : Color.secondary)
            .padding(.horizontal, TF.spacingSM)
            .frame(height: 26)
            .background(RoundedRectangle(cornerRadius: TF.cornerSM).fill(TF.surface))
            .overlay(RoundedRectangle(cornerRadius: TF.cornerSM).stroke(TF.line))
        }
        .buttonStyle(.plain)
        .disabled(!canTranslate)
        .help(translateHelp)
        .accessibilityLabel(L("翻译草稿", "Translate draft"))
        .accessibilityHint(translateHelp)

        if isTranslating {
            Button(L("取消", "Cancel"), action: cancelTranslation)
                .buttonStyle(.plain)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
        } else if translationOriginal != nil {
            Button(action: restoreTranslation) {
                Label(L("还原", "Restore"), systemImage: "arrow.uturn.backward")
                    .labelStyle(.titleAndIcon)
            }
            .buttonStyle(.plain)
            .font(.system(size: 11))
            .foregroundStyle(.secondary)
            .help(L("恢复翻译前的原文", "Restore the text before translation"))
        }
    }

    private var translateHelp: String {
        let target = ComposerTranslationDirection.target(for: textInput)
        return L(
            "用 Polish 模型将草稿翻译为\(target.label)；译文留在对话框，发送时不再润色",
            "Translate the draft to \(target.label) with the Polish model; the result stays here and is sent without polish"
        )
    }

    private var canTranslate: Bool {
        hasText && !isTranslating && !isPolishing
            && (voiceState.stage == .idle || voiceState.stage == .finalized)
    }

    private func translateDraft() {
        guard canTranslate else { return }
        let original = textInput
        let generation = inputGeneration
        isTranslating = true
        translationNote = nil
        polishFailure = nil
        translationTask?.cancel()
        translationTask = Task { @MainActor in
            defer { isTranslating = false }
            do {
                let result = try await ComposerTranslator.translate(original)
                guard !Task.isCancelled, inputGeneration == generation, textInput == original else { return }
                if result.unchanged {
                    translationNote = L("译文与原文相同，未改动", "Translation matches the original; nothing changed")
                    return
                }
                translationOriginal = original
                textInput = result.text
                hasPolished = true
                translationNote = L(
                    "已翻译为\(result.target.label) · 发送时不再润色",
                    "Translated to \(result.target.label) · sent without polish"
                )
            } catch is CancellationError {
                guard inputGeneration == generation else { return }
                translationNote = TranslationFailure.cancelled.message
            } catch {
                guard inputGeneration == generation else { return }
                translationNote = (error as? TranslationFailure)?.message ?? TranslationFailure.network.message
            }
        }
    }

    private func cancelTranslation() {
        translationTask?.cancel()
    }

    private func restoreTranslation() {
        guard let original = translationOriginal else { return }
        translationOriginal = nil
        translationNote = nil
        hasPolished = false
        textInput = original
    }

    // MARK: - Model badge

    /// Shows which provider and model Polish and Translate share. Clicking
    /// opens the Models settings tab. Re-reads when the saved LLM config changes.
    @ViewBuilder private var providerBadge: some View {
        let _ = TranslationSettings.shared.generation
        let provider = KeychainService.selectedLLMProvider
        let name = provider == .ollama ? "Ollama" : provider.displayName
        let config = KeychainService.llmDisplayConfiguration(for: provider)
        let configured = KeychainService.llmModelConfigured(for: provider)
        Button(action: openModelSettings) {
            HStack(spacing: 4) {
                Image(systemName: configured ? "cpu" : "exclamationmark.circle")
                    .font(.system(size: 10, weight: .semibold))
                Text(configured ? "\(name) · \(config.model)" : L("未配置 Polish 模型", "No Polish model"))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Image(systemName: "chevron.down")
                    .font(.system(size: 8, weight: .semibold))
            }
            .font(.system(size: 11))
            .foregroundStyle(.secondary)
            .padding(.horizontal, TF.spacingSM)
            .frame(height: 26)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: 220)
        .layoutPriority(5)
        .help(configured
            ? L("Polish 与翻译共用此模型 · \(config.endpoint)", "Polish and Translate share this model · \(config.endpoint)")
            : L("在设置 › 模型配置中填写 LLM 文本处理", "Configure LLM text processing in Settings › Models"))
        .accessibilityLabel(configured
            ? L("Polish 模型 \(name) \(config.model)，打开模型设置", "Polish model \(name) \(config.model), open model settings")
            : L("Polish 模型未配置，打开模型设置", "Polish model not configured, open model settings"))
    }

    private func openModelSettings() {
        AppDelegate.openSettingsAction?()
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            NotificationCenter.default.post(name: .navigateToModels, object: nil)
        }
    }

    // MARK: - Mic and buttons

    private var micButton: some View {
        composerIconButton(
            systemImage: micSystemImage,
            fill: micFill,
            foreground: isCapturing ? .white : .primary,
            help: micHelp,
            disabled: voiceState.stage == .processing || isTranslating || isPolishing,
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

    // MARK: - Derived state

    private var hasText: Bool {
        !textInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var isEditable: Bool {
        (voiceState.stage == .idle || voiceState.stage == .finalized) && !isTranslating && !isPolishing
    }

    private var canSend: Bool {
        hasText && !isTranslating && !isPolishing
            && (voiceState.stage == .idle || voiceState.stage == .finalized)
    }

    private var sendHelp: String {
        if isPolishing { return L("润色中", "Polishing") }
        if hasPolished || voiceState.stage == .finalized { return L("发送", "Send") }
        return L("润色后发送", "Polish, then send")
    }

    private var isCapturing: Bool {
        voiceState.stage == .listening || voiceState.stage == .counting
    }

    private var isVoiceExpanded: Bool {
        voiceState.stage != .idle || voiceError != nil
    }

    private var textAreaHeight: CGFloat {
        min(max(measuredTextHeight, ChatLayoutMetrics.inputTextMinHeight), ChatLayoutMetrics.inputMaxHeight)
    }

    private var composerHeight: CGFloat {
        textAreaHeight
            + ChatLayoutMetrics.inputToolbarHeight
            + (isVoiceExpanded ? ChatLayoutMetrics.voiceHeaderHeight : 0)
    }

    private var toolbarNote: String? {
        if isPolishing { return L("润色中…", "Polishing…") }
        if let polishFailure { return polishFailure }
        return translationNote
    }

    /// Any edit clears a stale Polish failure. Emptying the draft ends a
    /// finalized voice turn and forgets translation/polish state, so the next
    /// typed draft is polished again.
    private func draftDidChange() {
        polishFailure = nil
        guard textInput.isEmpty else { return }
        if voiceState.stage == .finalized {
            voiceState.reset()
        }
        hasPolished = false
        translationOriginal = nil
        translationNote = nil
    }

    // MARK: - Voice capture

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
        polishFailure = nil
        translationNote = nil
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

    // MARK: - Send and Polish

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
        guard !isTranslating, !isPolishing else { return }
        if voiceState.stage == .finalized || hasPolished {
            sendCurrentText()
            return
        }

        let generation = inputGeneration
        isPolishing = true
        polishFailure = nil
        Task { @MainActor in
            defer { isPolishing = false }
            guard inputGeneration == generation else { return }
            let corrected = SnippetStorage.applyEffective(to: textInput)
            if corrected != textInput {
                textInput = corrected
            }
            let outcome = await polishInPlace()
            guard !Task.isCancelled, inputGeneration == generation else { return }
            switch outcome {
            case .ready:
                sendCurrentText()
            case .failed(let reason):
                polishFailure = reason
            case .stale:
                break
            }
        }
    }

    /// Explicit bypass after a Polish failure: the user chose to send the
    /// draft as typed. Never taken silently.
    private func sendUnpolished() {
        polishFailure = nil
        hasPolished = true
        sendCurrentText()
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
        isPolishing = false
        polishFailure = nil
        translationTask?.cancel()
        translationTask = nil
        isTranslating = false
        translationOriginal = nil
        translationNote = nil
        measuredTextHeight = ChatLayoutMetrics.inputTextMinHeight
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
            // The restored prefix was never polished; the next send polishes it.
            hasPolished = false
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

    private enum PolishOutcome {
        case ready
        case failed(String)
        case stale
    }

    /// Runs Polish on the typed draft. `.ready` means the draft may be sent
    /// (polished, or no Polish model is configured). `.failed` keeps the draft
    /// and reports why, so an outage never sends unpolished text silently.
    @MainActor
    private func polishInPlace() async -> PolishOutcome {
        let text = textInput
        let generation = inputGeneration
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return .stale }
        guard let cfg = KeychainService.loadSelectedLLMConfig() else { return .ready }

        let llmCfg = cfg.toLLMConfig()
        let client = DoubaoChatClient(provider: KeychainService.selectedLLMProvider)
        let prompt: String
        if let m = appState.availableModes.first(where: { !$0.prompt.isEmpty }) {
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
            guard !Task.isCancelled, inputGeneration == generation, textInput == text else { return .stale }
            if !cleaned.isEmpty && cleaned != text {
                textInput = cleaned
            }
            hasPolished = true
            return .ready
        } catch {
            guard !Task.isCancelled, inputGeneration == generation, textInput == text else { return .stale }
            return .failed(Self.polishFailureMessage(error))
        }
    }

    private static func polishFailureMessage(_ error: Error) -> String {
        let detail: String
        if let llm = error as? LLMError {
            detail = llm.errorDescription ?? String(describing: llm)
        } else if let url = error as? URLError {
            detail = url.code == .timedOut
                ? L("请求超时", "request timed out")
                : L("网络错误 \(url.code.rawValue)", "network error \(url.code.rawValue)")
        } else {
            detail = error.localizedDescription
        }
        return L("润色失败：\(detail) · 再按发送重试", "Polish failed: \(detail) · press Send to retry")
    }

    // MARK: - Copy

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

/// AppKit text view bridge. Reports its laid-out content height so the
/// composer can grow upward; programmatic replacements go through the undo
/// manager when the view is editable, so Restore and ⌘Z both work.
private struct ChatInputTextView: NSViewRepresentable {
    @Binding var text: String
    let fontSize: CGFloat
    let isEditable: Bool
    let textColor: NSColor
    let onSubmit: () -> Void
    let onEscape: () -> Void
    let onContentHeightChange: (CGFloat) -> Void

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = CompressibleTextScrollView()
        scrollView.drawsBackground = false
        scrollView.hasVerticalScroller = true
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
        textView.postsFrameChangedNotifications = true

        context.coordinator.onContentHeightChange = onContentHeightChange
        context.coordinator.observeFrame(of: textView)

        scrollView.documentView = textView
        DispatchQueue.main.async { [weak textView] in
            guard let textView else { return }
            context.coordinator.reportHeight(of: textView)
        }
        return scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        guard let textView = scrollView.documentView as? SubmitAwareTextView else { return }
        let coordinator = context.coordinator
        coordinator.onContentHeightChange = onContentHeightChange
        textView.onSubmit = onSubmit
        textView.onEscape = onEscape
        textView.font = .systemFont(ofSize: fontSize)
        textView.isEditable = isEditable
        textView.textColor = textColor
        if textView.string != text {
            coordinator.applyingProgrammaticChange = true
            let whole = NSRange(location: 0, length: (textView.string as NSString).length)
            if textView.isEditable, textView.shouldChangeText(in: whole, replacementString: text) {
                textView.replaceCharacters(in: whole, with: text)
                textView.didChangeText()
            } else {
                textView.string = text
            }
            coordinator.applyingProgrammaticChange = false
        }
        DispatchQueue.main.async { [weak textView] in
            guard let textView else { return }
            coordinator.reportHeight(of: textView)
        }
    }

    static func dismantleNSView(_ scrollView: NSScrollView, coordinator: Coordinator) {
        coordinator.stopObserving()
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(text: $text)
    }

    /// Laid-out height of the text plus the container insets.
    static func contentHeight(of textView: NSTextView) -> CGFloat {
        let insets = textView.textContainerInset.height * 2
        if let layoutManager = textView.textLayoutManager {
            layoutManager.ensureLayout(for: layoutManager.documentRange)
            return ceil(layoutManager.usageBoundsForTextContainer.height) + insets
        }
        guard let container = textView.textContainer, let layoutManager = textView.layoutManager else {
            return ChatLayoutMetrics.inputTextMinHeight
        }
        layoutManager.ensureLayout(for: container)
        return ceil(layoutManager.usedRect(for: container).height) + insets
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        @Binding var text: String
        var onContentHeightChange: ((CGFloat) -> Void)?
        /// Set while `updateNSView` replaces the text, so the binding is not
        /// written back during a SwiftUI update pass.
        var applyingProgrammaticChange = false
        private var frameObserver: NSObjectProtocol?

        init(text: Binding<String>) {
            _text = text
        }

        func textDidChange(_ notification: Notification) {
            guard let textView = notification.object as? NSTextView else { return }
            if !applyingProgrammaticChange {
                text = textView.string
                reportHeight(of: textView)
            }
        }

        func reportHeight(of textView: NSTextView) {
            onContentHeightChange?(ChatInputTextView.contentHeight(of: textView))
        }

        func observeFrame(of textView: NSTextView) {
            stopObserving()
            frameObserver = NotificationCenter.default.addObserver(
                forName: NSView.frameDidChangeNotification,
                object: textView,
                queue: .main
            ) { [weak self, weak textView] _ in
                guard let self, let textView else { return }
                MainActor.assumeIsolated {
                    self.reportHeight(of: textView)
                }
            }
        }

        func stopObserving() {
            if let frameObserver {
                NotificationCenter.default.removeObserver(frameObserver)
            }
            frameObserver = nil
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
