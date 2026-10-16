import SwiftUI

@main
struct VoxLogApp: App {

    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        MenuBarExtra {
            MenuBarContent()
                .environment(appDelegate.appState)
                .environment(appDelegate.appUpdater)
                .environment(appDelegate.engineCoordinator)
                .environment(appDelegate.agentStatusStore)
        } label: {
            MenuBarStatusLabel()
                .environment(appDelegate.appState)
                .environment(appDelegate.agentStatusStore)
        }

        // Main chat window — Vox Agent primary UI, opens on launch
        Window(L("Vox Agent", "Vox Agent"), id: "chat") {
            ChatWindowView(taskStore: appDelegate.taskStore)
                .environment(appDelegate.appState)
                .environment(appDelegate.appUpdater)
                .environment(appDelegate.engineCoordinator)
                .environment(appDelegate.agentStatusStore)
        }
        .defaultSize(width: ChatLayoutMetrics.windowStableMinWidth, height: 700)
        .defaultPosition(.center)
        .windowResizability(.contentSize)

        Window(L("登录 Vox Agent", "Sign in to Vox Agent"), id: "login") {
            LoginView()
                .environment(appDelegate.appState)
                .environment(appDelegate.appUpdater)
        }
        .windowResizability(.contentSize)
        .defaultPosition(.center)
        .windowStyle(.hiddenTitleBar)

        // Settings — opened from ⚙️ in sidebar
        Window(L("Vox Agent 设置", "Vox Agent Settings"), id: "settings") {
            SettingsView()
                .environment(appDelegate.appState)
                .environment(appDelegate.appUpdater)
        }
        .defaultSize(width: 1200, height: 800)
        .defaultPosition(.center)
        .windowStyle(.hiddenTitleBar)

        Window(L("Vox Agent 设置向导", "Vox Agent Setup"), id: "setup") {
            SetupWizardView()
                .environment(appDelegate.appState)
                .environment(appDelegate.appUpdater)
        }
        .windowResizability(.contentSize)
        .defaultPosition(.center)
        .windowStyle(.hiddenTitleBar)
    }
}

// MARK: - App Delegate

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    let appState = AppState()
    let appUpdater = AppUpdater()
    private let startSoundDelay: Duration = .milliseconds(200)
    private var floatingBarController: FloatingBarController?
    private var terminationReplySent = false
    private let hotkeyManager = HotkeyManager()
    let session = RecognitionSession()
    /// Agent Engine coordinator — manages Python engine process + WebSocket
    let engineCoordinator = EngineCoordinator()
    let taskStore = TaskStore()
    let agentStatusStore = AgentStatusStore()
    private var agentStatusRefreshTask: Task<Void, Never>?
    /// Static access for chat window mic button
    static var shared: AppDelegate?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSLog("[VoxLog] applicationDidFinishLaunching")
        Self.shared = self
        // VoxLog is a regular app (always show in Dock)
        NSApp.setActivationPolicy(.regular)
        KeychainService.migrateIfNeeded()
        HotwordStorage.migrateIfNeeded()
        SnippetStorage.migrateIfNeeded()

        // Sync hotwords to Volcengine cloud table (async, non-blocking)
        VolcHotwordSyncManager.syncIfNeeded()

        DebugFileLogger.startSession()
        DebugFileLogger.log("applicationDidFinishLaunching")

        // VoxLog: migrate v2 data (history + dictionary) on first launch
        if V2Migrator.needsMigration {
            Task {
                let count = await V2Migrator.migrate()
                NSLog("[VoxLog] V2 migration: %d records imported", count)
            }
        }
        // Global capture remains visible even when the Vox Agent window is not frontmost.
        floatingBarController = FloatingBarController(state: appState)

        // Bridge ASR events → AppState for floating bar display
        let session = self.session

        // 历史记录字数迁移（用 session 自带的 historyStore，迁移后 UI 能刷新）
        Task { await session.historyStore.migrateCharacterCounts() }
        let appState = self.appState
        appState.feishuOutbox.onVoiceDraftCancelled = { [weak self] id in
            guard let self else { return }
            if appState.activeFeishuVoiceID == id { appState.cancel() }
            Task { await self.session.cancelFeishuDraft(id) }
        }
        let startSoundDelay = self.startSoundDelay

        SoundFeedback.warmUp()

        // Pre-warm audio subsystem so the first recording starts instantly
        Task { await session.warmUp() }

        // Bridge audio level → isolated meter (no SwiftUI observation overhead)
        Task {
            await session.setOnAudioLevel { level in
                appState.audioLevel.current = level
            }
        }

        Task {
            await session.setOnASREvent { received in
                Task { @MainActor in
                    let event: RecognitionEvent
                    if case .feishuDraftEvent(let id, let scoped) = received {
                        guard appState.activeFeishuVoiceID == id else { return }
                        event = scoped
                    } else { event = received }
                    switch event {
                    case .feishuDraftEvent: return
                    case .ready:
                        NSLog("[VoxLog] ready event received")
                        DebugFileLogger.log("ready event received, current barPhase=\(String(describing: appState.barPhase))")
                        appState.markRecordingReady()
                        Task { @MainActor in
                            NSLog("[VoxLog] playStart scheduled")
                            DebugFileLogger.log("playStart scheduled delayMs=200")
                            try? await Task.sleep(for: startSoundDelay)
                            guard appState.barPhase == .recording else {
                                DebugFileLogger.log("playStart aborted, barPhase=\(String(describing: appState.barPhase))")
                                return
                            }
                            NSLog("[VoxLog] playStart firing")
                            DebugFileLogger.log("playStart firing")
                            SoundFeedback.playStart()
                            // Lower volume after start sound finishes playing
                            let targetVolumePercent = UserDefaults.standard.integer(forKey: "tf_volumeReduction")
                            if targetVolumePercent >= 0 {
                                try? await Task.sleep(for: .milliseconds(500))
                                guard appState.barPhase == .recording else { return }
                                SystemVolumeManager.lower(to: Float(targetVolumePercent) / 100.0)
                            }
                        }
                    case .transcript(let transcript):
                        appState.setLiveTranscript(transcript)
                    case .completed:
                        appState.stopRecording()
                        self.hotkeyManager.isProcessing = false
                        self.safeResetHotkeyState()
                    case .processingResult(let text):
                        appState.showProcessingResult(text)
                        self.hotkeyManager.isProcessing = true
                    case .finalized(let text, let injection):
                        appState.finalize(text: text, outcome: injection)
                        self.hotkeyManager.isProcessing = false
                        self.safeResetHotkeyState()
                    case .feishuDraftFinalized(let id, let text):
                        guard appState.activeFeishuVoiceID == id else { return }
                        appState.feishuOutbox.finishVoiceDraft(id, text: text)
                        appState.cancel()
                        self.hotkeyManager.isProcessing = false
                        self.safeResetHotkeyState()
                    case .error(let error):
                        appState.showError(self.userFacingMessage(for: error))
                        self.hotkeyManager.isProcessing = false
                        self.safeResetHotkeyState()
                    }
                }
            }
        }

        // Start periodic update checking
        UpdateChecker.shared.startPeriodicChecking(appState: appState)
        appUpdater.checkPostUpdateStatus()

        // Reconcile current mode against the active provider before hotkeys are registered.
        refreshModeAvailability()

        // Re-register when modes change in Settings
        NotificationCenter.default.addObserver(
            forName: .modesDidChange,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { [weak self] in
                self?.refreshModeAvailability()
            }
        }

        NotificationCenter.default.addObserver(
            forName: .asrProviderDidChange,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { [weak self] in
                self?.refreshModeAvailability()
            }
        }

        // Suppress/resume hotkeys during hotkey recording
        NotificationCenter.default.addObserver(
            forName: .hotkeyRecordingDidStart,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { [weak self] in
                self?.hotkeyManager.isSuppressed = true
            }
        }
        NotificationCenter.default.addObserver(
            forName: .hotkeyRecordingDidEnd,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { [weak self] in
                self?.hotkeyManager.isSuppressed = false
            }
        }

        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.startHotkeyWithRetry()
        }

        Task { [weak self] in
            while !Task.isCancelled {
                guard let account = self?.appState.feishuAccount else { return }
                await account.maintainSession()
                try? await Task.sleep(for: .seconds(60))
            }
        }
        let loginSession = LoginSession.load()
        // Show login first. It preserves existing history/config and only records the selected workspace mode.
        if !loginSession.hasCompletedLogin {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                MainActor.assumeIsolated {
                    _ = NSApp.sendAction(Selector(("showLoginWindow:")), to: nil, from: nil)
                    NSApp.activate(ignoringOtherApps: true)
                }
            }
        } else if !appState.hasCompletedSetup {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                MainActor.assumeIsolated {
                    _ = NSApp.sendAction(Selector(("showSetupWindow:")), to: nil, from: nil)
                }
            }
        } else {
            // Open the main chat window on launch
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                MainActor.assumeIsolated {
                    Self.openChatAction?()
                    NSApp.activate(ignoringOtherApps: true)
                }
            }
        }

        // Start SenseVoice Python server if local ASR is selected
        let needsLocalServer = KeychainService.selectedASRProvider == .sherpa
        if needsLocalServer {
            // Preserve the user's local provider selection. Missing/corrupt models
            // must not silently route future audio to a cloud service.
            Task {
                do {
                    try await SenseVoiceServerManager.shared.start()
                } catch {
                    NSLog("[App] SenseVoice server start failed: %@", String(describing: error))
                }
            }
        }

        // Start Vox Agents Engine (Python process + WebSocket)
        // The engine provides task scheduling, agent execution, and tool management
        startAgentEngine()
        startAgentStatusRefresh()

        // Auto-sync on launch (3 seconds delay to let UI settle)
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak self] in
            guard let self else { return }
            NSLog("[VoxLog] Auto-sync on launch")
            self.appState.performSync()
        }

        // Check if menu bar icon is hidden by macOS 26+ "Allow in Menu Bar" setting
        checkMenuBarVisibility()

        // Listen for Dock icon preference changes
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(dockIconPreferenceChanged(_:)),
            name: UserDefaults.didChangeNotification,
            object: nil
        )
    }

    private func refreshModeAvailability() {
        let provider = KeychainService.selectedASRProvider
        appState.reconcileCurrentMode(for: provider)
        registerHotkeys(for: provider)
    }

    private func registerHotkeys(for provider: ASRProvider) {
        let availableModes = appState.availableModes
        let modes = ASRProviderRegistry.supportedModes(from: availableModes, for: provider)
        let bindings: [ModeBinding] = modes.compactMap { mode in
            guard let code = mode.hotkeyCode else { return nil }
            let modifiers = CGEventFlags(rawValue: mode.hotkeyModifiers ?? 0)
            let capturedMode = mode
            return ModeBinding(
                modeId: mode.id,
                keyCode: CGKeyCode(code),
                modifiers: modifiers,
                style: capturedMode.hotkeyStyle,
                onStart: { [weak self] in
                    guard let self else { return }
                    guard MainActor.assumeIsolated({ self.appState.activeFeishuVoiceID == nil }) else {
                        MainActor.assumeIsolated { self.hotkeyManager.resetActiveState() }
                        return
                    }

                    // Safety: if already recording, the toggle state is out of sync.
                    // Redirect to stop so we don't discard accumulated text.
                    let alreadyRecording = MainActor.assumeIsolated {
                        self.appState.barPhase == .recording || self.appState.barPhase == .preparing
                    }
                    if alreadyRecording {
                        NSLog("[VoxLog] >>> HOTKEY: toggle desync – onStart while recording, redirecting to STOP")
                        DebugFileLogger.log("hotkey toggle desync: onStart while recording, redirecting to stop")
                        MainActor.assumeIsolated { self.hotkeyManager.resetActiveState() }
                        MainActor.assumeIsolated { self.appState.stopRecording() }
                        Task { await self.session.stopRecording() }
                        return
                    }

                    let selectedProvider = KeychainService.selectedASRProvider
                    let resolvedMode = ASRProviderRegistry.resolvedMode(for: capturedMode, provider: selectedProvider)
                    let effectiveMode = availableModes.first(where: { $0.id == resolvedMode.id }) ?? resolvedMode
                    NSLog("[VoxLog] >>> HOTKEY: Record START (mode: %@)", effectiveMode.name)
                    DebugFileLogger.log("hotkey record start mode=\(effectiveMode.name)")
                    Task { @MainActor in
                        self.appState.currentMode = effectiveMode
                        self.appState.startRecording()
                    }
                    let targetAgentID = MainActor.assumeIsolated {
                        self.appState.voiceTargetAgentID
                    }
                    Task {
                        await self.session.setAgent(targetAgentID)
                        await self.session.startRecording(mode: effectiveMode)
                    }
                },
                onStop: { [weak self] in
                    guard let self else { return }
                    guard MainActor.assumeIsolated({ self.appState.activeFeishuVoiceID == nil }) else { return }
                    NSLog("[VoxLog] >>> HOTKEY: Record STOP")
                    DebugFileLogger.log("hotkey record stop")
                    MainActor.assumeIsolated { self.appState.stopRecording() }
                    Task { await self.session.stopRecording() }
                }
            )
        }
        hotkeyManager.registerBindings(bindings)

        // Cross-mode stop: user pressed mode B's key while mode A was recording.
        // Switch to mode B and stop, so the recording is processed with mode B.
        hotkeyManager.onCrossModeStop = { [weak self] newModeId in
            guard let self else { return }
            guard MainActor.assumeIsolated({ self.appState.activeFeishuVoiceID == nil }) else { return }
            guard let newMode = availableModes.first(where: { $0.id == newModeId }) else { return }
            let selectedProvider = KeychainService.selectedASRProvider
            let resolvedMode = ASRProviderRegistry.resolvedMode(for: newMode, provider: selectedProvider)
            let effectiveMode = availableModes.first(where: { $0.id == resolvedMode.id }) ?? resolvedMode
            NSLog("[VoxLog] >>> HOTKEY: Cross-mode stop → %@", effectiveMode.name)
            DebugFileLogger.log("hotkey cross-mode stop → \(effectiveMode.name)")
            MainActor.assumeIsolated {
                self.appState.currentMode = effectiveMode
                self.appState.stopRecording()
            }
            Task {
                await self.session.switchMode(to: effectiveMode)
                await self.session.stopRecording()
            }
        }

        hotkeyManager.onTargetCycle = { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                await self.cycleVoiceTarget()
            }
        }

        // ESC is a real cancel while capturing. Once processing has begun, it only
        // suppresses system input because the durable history event may already exist.
        hotkeyManager.onESCAbort = { [weak self] in
            guard let self else { return }
            let phase = appState.barPhase
            guard phase == .recording || phase == .processing || phase == .preparing else {
                return  // Not in an active session, ignore ESC
            }
            if let id = self.appState.feishuOutbox.voiceDraftID {
                self.appState.cancel()
                self.hotkeyManager.resetActiveState()
                Task { await self.session.cancelFeishuDraft(id) }
                return
            }
            if phase == .preparing || phase == .recording {
                NSLog("[VoxLog] >>> HOTKEY: ESC cancel capture (phase=%@)", String(describing: phase))
                DebugFileLogger.log("hotkey ESC cancel capture phase=\(phase)")
                MainActor.assumeIsolated {
                    self.hotkeyManager.resetActiveState()
                    self.appState.cancel()
                }
                Task { await self.session.cancelRecording() }
            } else {
                NSLog("[VoxLog] >>> HOTKEY: ESC skip system input")
                DebugFileLogger.log("hotkey ESC skip system input")
                Task { await self.session.abortInjection() }
            }
        }

        // Sync ESC abort enabled setting to HotkeyManager
        syncESCAbortSetting()
        NotificationCenter.default.addObserver(
            forName: UserDefaults.didChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { [weak self] in
                self?.syncESCAbortSetting()
            }
        }
    }

    private func cycleVoiceTarget() async {
        let scanned = await WorkspaceScanner.shared.scanAgents()
        let candidates = (scanned.isEmpty ? Agent.defaults : scanned)
            .filter { !$0.isArchived && $0.parentId == nil }
        guard !candidates.isEmpty else { return }

        let currentID = appState.voiceTargetAgentID
        let currentIndex = candidates.firstIndex(where: { $0.id == currentID }) ?? -1
        let nextAgent = candidates[(currentIndex + 1) % candidates.count]
        appState.setVoiceTarget(id: nextAgent.id, name: nextAgent.name)
        await session.setAgent(nextAgent.id)
        SoundFeedback.playStart()
    }

    private func syncESCAbortSetting() {
        // Default to true if not set (key doesn't exist)
        if UserDefaults.standard.object(forKey: "tf_escAbortEnabled") == nil {
            hotkeyManager.isESCAbortEnabled = true
        } else {
            hotkeyManager.isESCAbortEnabled = UserDefaults.standard.bool(forKey: "tf_escAbortEnabled")
        }
    }

    private var retryTimer: Timer?
    private var hotkeyRetryCount = 0

    private func startHotkeyWithRetry() {
        let success = hotkeyManager.start()
        NSLog("[VoxLog] Hotkey setup: %@", success ? "OK" : "FAILED (need Accessibility permission)")

        if success {
            retryTimer?.invalidate()
            retryTimer = nil
            hotkeyRetryCount = 0
            return
        }

        // VoxLog: only prompt once, don't keep retrying with popups.
        // Check silently — if not granted, log it but don't force a popup.
        if !PermissionManager.hasAccessibilityPermission {
            NSLog("[VoxLog] Accessibility not granted. Hotkeys won't work. User can enable in System Settings.")
            // Only prompt once on first launch
            if !UserDefaults.standard.bool(forKey: "vl_accessibilityPrompted") {
                PermissionManager.promptAccessibilityPermission()
                UserDefaults.standard.set(true, forKey: "vl_accessibilityPrompted")
            }
        }

        hotkeyRetryCount = 0
        retryTimer?.invalidate()
        retryTimer = Timer.scheduledTimer(
            timeInterval: 5.0,
            target: self,
            selector: #selector(handleHotkeyRetry(_:)),
            userInfo: nil,
            repeats: true
        )
    }

    @objc
    private func handleHotkeyRetry(_ timer: Timer) {
        hotkeyRetryCount += 1
        // VoxLog: stop retrying after 3 attempts to avoid annoying the user
        if hotkeyRetryCount >= 3 {
            timer.invalidate()
            retryTimer = nil
            hotkeyRetryCount = 0
            NSLog("[VoxLog] Hotkey retry limit reached, stopping. User can use mic button instead.")
            return
        }
        if PermissionManager.hasAccessibilityPermission {
            let ok = hotkeyManager.start()
            NSLog("[VoxLog] Hotkey retry #%d: %@", hotkeyRetryCount, ok ? "OK" : "still failing")
            if ok {
                timer.invalidate()
                retryTimer = nil
                hotkeyRetryCount = 0
            }
        }
    }

    private func showRestartAlert() {
        let alert = NSAlert()
        alert.messageText = NSLocalizedString("辅助功能权限已开启，但快捷键未生效", comment: "")
        alert.informativeText = NSLocalizedString(
            "macOS 有时需要重启应用才能激活全局快捷键。点击「重启」自动重启 Vox Agent。",
            comment: ""
        )
        alert.addButton(withTitle: NSLocalizedString("重启", comment: ""))
        alert.addButton(withTitle: NSLocalizedString("稍后", comment: ""))
        alert.alertStyle = .informational

        if alert.runModal() == .alertFirstButtonReturn {
            // Relaunch the app
            let url = Bundle.main.bundleURL
            let task = Process()
            task.launchPath = "/usr/bin/open"
            task.arguments = ["-n", url.path]
            try? task.run()
            NSApp.terminate(nil)
        }
    }

    @objc
    private func dockIconPreferenceChanged(_ notification: Notification) {
        let showDock = UserDefaults.standard.object(forKey: "tf_showDockIcon") as? Bool ?? true
        let current = NSApp.activationPolicy()
        let desired: NSApplication.ActivationPolicy = showDock ? .regular : .accessory
        if current != desired {
            NSApp.setActivationPolicy(desired)
        }
    }

    // MARK: - Menu Bar Visibility Check (macOS 26+)

    /// On macOS 26 Tahoe, System Settings > Menu Bar > "Allow in Menu Bar" can hide
    /// third-party status items by rendering them offscreen. Detect this and alert the user.
    private func checkMenuBarVisibility() {
        // Only check on macOS 26+ where this feature exists
        guard ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26 else { return }

        // Delay to give SwiftUI MenuBarExtra time to create the status item
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak self] in
            guard let self else { return }
            MainActor.assumeIsolated {
                self.performMenuBarCheck()
            }
        }
    }

    private func performMenuBarCheck() {
        // Find status bar windows belonging to our app.
        // SwiftUI's MenuBarExtra creates an NSStatusBarWindow with a button inside.
        let statusBarWindows = NSApp.windows.filter {
            $0.className.contains("NSStatusBar")
        }

        let isVisible: Bool
        if statusBarWindows.isEmpty {
            // No status bar window at all — icon wasn't created
            isVisible = false
        } else {
            // Check if any status bar window is in a reasonable screen position.
            // macOS 26 moves hidden items far offscreen (e.g. y < -10000).
            // Check against ALL screens to handle multi-monitor setups correctly.
            let allScreens = NSScreen.screens
            isVisible = statusBarWindows.contains { window in
                let frame = window.frame
                return allScreens.contains { screen in
                    let sf = screen.frame
                    return frame.origin.x >= sf.minX - 100
                        && frame.origin.x <= sf.maxX + 100
                        && frame.origin.y >= sf.minY - 100
                }
            }
        }

        guard !isVisible else { return }

        NSLog("[VoxLog] Menu bar icon appears hidden by system settings")

        let alert = NSAlert()
        alert.messageText = L(
            "菜单栏图标被隐藏",
            "Menu Bar Icon Hidden"
        )
        alert.informativeText = L(
            "macOS 的菜单栏设置可能隐藏了 Vox Agent 图标。\n\n请前往 系统设置 > 菜单栏，在「允许在菜单栏中显示」列表中开启 Vox Agent。",
            "macOS may have hidden the Vox Agent icon.\n\nGo to System Settings > Menu Bar and enable Vox Agent in the 'Allow in Menu Bar' list."
        )
        alert.alertStyle = .warning
        alert.addButton(withTitle: L("打开系统设置", "Open System Settings"))
        alert.addButton(withTitle: L("稍后处理", "Later"))

        NSApp.activate(ignoringOtherApps: true)
        let response = alert.runModal()

        if response == .alertFirstButtonReturn {
            // Open Menu Bar settings (macOS 26+)
            if let url = URL(string: "x-apple.systempreferences:com.apple.MenuBar-Settings") {
                NSWorkspace.shared.open(url)
            }
        }
    }

    /// Stored by MenuBarContent so AppDelegate can open the settings window.
    static var openSettingsAction: (() -> Void)?
    static var openChatAction: (() -> Void)?
    static var openLoginAction: (() -> Void)?

    // MARK: - Graceful Quit with Sync

    /// macOS calls this BEFORE willTerminate. Returning .terminateLater lets us
    /// finish the sync asynchronously, then call `NSApp.reply(toApplicationShouldTerminate: true)`.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        terminationReplySent = false
        // If already syncing (e.g. auto-sync on launch still running), wait for it
        guard !appState.isSyncing else {
            NSLog("[VoxLog] Quit requested while sync in progress, waiting...")
            waitForSyncThenTerminate()
            return .terminateLater
        }

        // Start a final sync
        NSLog("[VoxLog] Final sync before quit")
        appState.isSyncing = true
        Task.detached {
            let summary = await AppState.performPreferredSyncSummary()
            NSLog("[VoxLog] Quit sync: %@", summary)
            await MainActor.run {
                self.appState.isSyncing = false
                self.replyToTerminationOnce(reason: "final sync completed")
            }
        }

        // Safety timeout: if sync takes >8 seconds, force quit anyway
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) {
            self.replyToTerminationOnce(reason: "final sync timed out")
        }

        return .terminateLater
    }

    private func waitForSyncThenTerminate() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            while self.appState.isSyncing {
                try? await Task.sleep(nanoseconds: 300_000_000)
            }
            self.replyToTerminationOnce(reason: "in-flight sync completed")
        }
        // Safety timeout
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) {
            self.replyToTerminationOnce(reason: "in-flight sync timed out")
        }
    }

    private func replyToTerminationOnce(reason: String) {
        guard !terminationReplySent else { return }
        terminationReplySent = true
        NSLog("[VoxLog] Allowing quit: %@", reason)
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        agentStatusRefreshTask?.cancel()
        // Stop Agent Engine process
        engineCoordinator.stop()
        SenseVoiceServerManager.killAllServerProcesses()
    }

    // MARK: - Agent Engine

    private func startAgentEngine() {
        // Wire event handler to TaskStore
        Task { await taskStore.ensureLoaded() }
        engineCoordinator.eventHandler.taskStore = taskStore

        // Check for existing engine on known ports (async to avoid blocking main thread)
        Task { [self] in
            for checkPort in [18900, 18901, 18902, 18903] {
                if await self.checkEngineHealth(port: checkPort) {
                    await MainActor.run {
                        NSLog("[VoxLog] ✅ Engine found on port %d, connecting...", checkPort)
                        self.engineCoordinator.port = checkPort
                        self.engineCoordinator.state = .running
                        self.engineCoordinator.websocket.onEvent = { [weak self] event in
                            self?.engineCoordinator.eventHandler.handle(event)
                        }
                        self.engineCoordinator.websocket.connect(port: checkPort)
                    }
                    return
                }
            }

            // No existing engine found — try to start one
            await MainActor.run {
                NSLog("[VoxLog] No engine found, starting...")
                self.engineCoordinator.start()
            }

            // Verify after delay
            try? await Task.sleep(for: .seconds(5))
            if self.engineCoordinator.state != .running {
                // Retry once
                for checkPort in [18900, 18901, 18902, 18903] {
                    if await self.checkEngineHealth(port: checkPort) {
                        await MainActor.run {
                            NSLog("[VoxLog] ✅ Engine found on retry, port %d", checkPort)
                            self.engineCoordinator.port = checkPort
                            self.engineCoordinator.state = .running
                            self.engineCoordinator.websocket.connect(port: checkPort)
                        }
                        return
                    }
                }
                NSLog("[VoxLog] ❌ Engine still not running. Start manually: ./dev.sh")
            }
        }
    }

    private func startAgentStatusRefresh() {
        agentStatusRefreshTask?.cancel()
        agentStatusRefreshTask = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.taskStore.ensureLoaded()

            while !Task.isCancelled {
                await self.agentStatusStore.refresh(
                    taskStore: self.taskStore,
                    engine: self.engineCoordinator
                )
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    private func checkEngineHealth(port: Int) async -> Bool {
        guard let url = URL(string: "http://127.0.0.1:\(port)/health") else { return false }
        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 2.0
            let (data, response) = try await URLSession.shared.data(for: request)
            if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200,
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               json["status"] as? String == "ok" {
                return true
            }
        } catch { }
        return false
    }

    // MARK: - URL Scheme Handling

    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls {
            guard url.scheme == "voxlog" else { continue }
            switch url.host {
            case "reload-vocabulary":
                NSLog("[VoxLog] URL command: reload-vocabulary")
                SenseVoiceServerManager.syncHotwordsAndRestart()
            case "auth":
                NSLog("[VoxLog] URL command: auth (no-op, code-based auth now)")
            default:
                NSLog("[VoxLog] Unknown URL command: \(url)")
            }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            Self.openChatAction?()
        }
        NSApp.activate(ignoringOtherApps: true)
        return true
    }

    /// Only reset hotkey state when no new recording is in progress.
    /// Prevents a stale finalized/completed event from corrupting the toggle
    /// state of a recording that started after the event was emitted.
    private func safeResetHotkeyState() {
        let phase = appState.barPhase
        if phase == .recording || phase == .preparing {
            DebugFileLogger.log("safeResetHotkeyState: skipped (barPhase=\(phase))")
            return
        }
        hotkeyManager.resetActiveState()
    }

    private func userFacingMessage(for error: Error) -> String {
        if let captureError = error as? AudioCaptureError,
           let description = captureError.errorDescription {
            return description
        }

        if let localizedError = error as? LocalizedError,
           let description = localizedError.errorDescription,
           !description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return description
        }

        let nsError = error as NSError
        if let description = nsError.userInfo[NSLocalizedDescriptionKey] as? String,
           !description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return description
        }

        return L("录音启动失败", "Failed to start recording")
    }
}

// MARK: - Menu Bar Content

struct MenuBarContent: View {

    @Environment(AppState.self) private var appState
    @Environment(EngineCoordinator.self) private var engine
    @Environment(AgentStatusStore.self) private var agentStatusStore
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openWindow) private var openSettingsWindow
    @AppStorage("tf_language") private var language = AppLanguage.systemDefault

    var body: some View {
        VStack(alignment: .leading, spacing: TF.spacingSM) {
            HStack(spacing: TF.spacingXS) {
                Circle()
                    .fill(statusColor)
                    .frame(width: TF.spacingSM, height: TF.spacingSM)
                    .accessibilityHidden(true)
                Text(statusText)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }

            StatusCountsRow(
                snapshot: agentStatusStore.snapshot,
                onAwaitingApproval: focusPendingApproval
            )
            EngineStatusLabel(status: agentStatusStore.snapshot.engineStatus)
        }
        .padding(.horizontal, TF.spacingMD)
        .padding(.vertical, TF.spacingSM)

        Divider()

        // Mode hotkey hints (click to open settings)
        ForEach(appState.availableModes) { mode in
            Button {
                openSettingsWindow(id: "settings")
                NSApp.activate(ignoringOtherApps: true)
                NotificationCenter.default.post(
                    name: .navigateToMode, object: mode.id
                )
            } label: {
                let hotkey = mode.hotkeyCode.map {
                    HotkeyRecorderView.keyDisplayName(keyCode: $0, modifiers: mode.hotkeyModifiers)
                }
                Text("\(mode.name)  [\(hotkey ?? L("未绑定", "Unbound"))]")
            }
        }

        Divider()

        Button(L("设置向导...", "Setup Wizard...")) {
            openWindow(id: "setup")
            NSApp.activate(ignoringOtherApps: true)
        }

        Button(L("团队入口...", "Team Entry...")) {
            openWindow(id: "login")
            NSApp.activate(ignoringOtherApps: true)
        }

        Button(L("打开 Vox Agent", "Open Vox Agent")) {
            openSettingsWindow(id: "chat")
            NSApp.activate(ignoringOtherApps: true)
        }
        .keyboardShortcut("o", modifiers: .command)

        Button(L("偏好设置...", "Preferences...")) {
            openSettingsWindow(id: "settings")
            NSApp.activate(ignoringOtherApps: true)
        }
        .keyboardShortcut(",", modifiers: .command)

        Divider()

        Button(L("退出 Vox Agent", "Quit Vox Agent")) {
            NSApplication.shared.terminate(nil)
        }
        .keyboardShortcut("q", modifiers: .command)

        // Force re-render when language changes
        let _ = language

        // Register window openers for programmatic access
        let _ = {
            AppDelegate.openSettingsAction = { [openSettingsWindow] in
                openSettingsWindow(id: "settings")
            }
            AppDelegate.openChatAction = { [openSettingsWindow] in
                openSettingsWindow(id: "chat")
            }
            AppDelegate.openLoginAction = { [openWindow] in
                openWindow(id: "login")
            }
        }()
    }

    private var statusColor: Color {
        switch appState.barPhase {
        case .preparing: return TF.recording
        case .recording: return TF.recording
        case .processing: return TF.amber
        case .done: return TF.success
        case .error: return TF.danger
        case .hidden: return agentStatusStore.snapshot.primaryStatus.color
        }
    }

    private func focusPendingApproval() {
        guard let approval = engine.eventHandler.pendingApprovals.first else { return }
        appState.pendingApprovalFocusID = approval.id
        openSettingsWindow(id: "chat")
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.main.async {
            NotificationCenter.default.post(
                name: .focusPermissionApproval,
                object: approval.id
            )
        }
    }

    private var statusText: String {
        switch appState.barPhase {
        case .preparing: return L("录制中", "Recording")
        case .recording: return L("录制中", "Recording")
        case .processing: return appState.currentMode.processingLabel
        case .done: return L("完成", "Done")
        case .error: return L("错误", "Error")
        case .hidden: return agentStatusStore.snapshot.primaryStatus.label
        }
    }
}

struct MenuBarStatusLabel: View {
    @Environment(AppState.self) private var appState
    @Environment(AgentStatusStore.self) private var agentStatusStore

    var body: some View {
        HStack(spacing: TF.spacingXS) {
            Image(systemName: systemImage)
            if let badgeStatus {
                Circle().fill(badgeStatus.color)
                    .frame(width: 6, height: 6)
                    .accessibilityHidden(true)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Vox Agent, \(statusLabel)")
    }

    private var badgeStatus: TF.Status? {
        switch appState.barPhase {
        case .error:
            return .failed
        case .hidden:
            let status = agentStatusStore.snapshot.primaryStatus
            return status == .awaitingApproval || status == .failed ? status : nil
        case .preparing, .recording, .processing, .done:
            return nil
        }
    }

    private var systemImage: String {
        switch appState.barPhase {
        case .preparing, .recording: return "mic.fill"
        case .processing: return "waveform"
        case .done: return TF.Status.completed.symbolName
        case .error: return TF.Status.failed.symbolName
        case .hidden: return agentStatusStore.snapshot.primaryStatus.symbolName
        }
    }

    private var statusLabel: String {
        switch appState.barPhase {
        case .preparing, .recording: return L("录制中", "Recording")
        case .processing: return appState.currentMode.processingLabel
        case .done: return TF.Status.completed.label
        case .error: return TF.Status.failed.label
        case .hidden: return agentStatusStore.snapshot.primaryStatus.label
        }
    }
}
