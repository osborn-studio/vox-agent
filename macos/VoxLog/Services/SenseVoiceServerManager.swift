import Foundation
import os

/// Manages the local Qwen3-ASR Python server process.
/// SenseVoice streaming is now handled natively via sherpa-onnx (SenseVoiceASRClient).
actor SenseVoiceServerManager {
    static let shared = SenseVoiceServerManager()

    private static let processRegistry = ASRProcessRegistry()

    /// Synchronous termination of this App instance's actual child processes only.
    /// Legacy PID files are not authority: their PIDs may have been reused.
    nonisolated static func killAllServerProcesses() {
        processRegistry.terminateAll()
        currentQwen3Port = nil
    }

    /// Write effective hotwords (builtin + user) to hotwords.txt for Qwen3 server.
    nonisolated static func syncHotwordsFile() {
        let words = HotwordStorage.loadEffective()
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let dir = appSupport.appendingPathComponent("VoxLog")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let path = dir.appendingPathComponent("hotwords.txt")
        let content = words.joined(separator: "\n")
        try? content.write(to: path, atomically: true, encoding: .utf8)
        DebugFileLogger.log("Synced \(words.count) hotwords to hotwords.txt")
    }

    /// Persist vocabulary for other consumers. Local Qwen ignores vocabulary prompts,
    /// so restarting its helper here would only interrupt an active recording.
    nonisolated static func syncHotwordsAndRestart() {
        syncHotwordsFile()
    }

    /// Port of the running Qwen3-ASR server.
    private static let _portLock = OSAllocatedUnfairLock(initialState: Int?(nil))
    static var currentQwen3Port: Int? {
        get { _portLock.withLock { $0 } }
        set { _portLock.withLock { $0 = newValue } }
    }

    private let logger = Logger(subsystem: "com.voxlog.sensevoice", category: "ServerManager")

    private var qwen3Process: Process?
    private(set) var qwen3Port: Int?
    private var qwen3StdoutPipe: Pipe?
    private var qwen3PortReader: ASRPortReader?
    private var launchGeneration: UInt64 = 0
    private var modelPreparation: Task<URL?, Error>?

    var isRunning: Bool { qwen3Process?.isRunning ?? false }

    var qwen3WSURL: URL? {
        guard let qwen3Port else { return nil }
        return URL(string: "ws://127.0.0.1:\(qwen3Port)/ws")
    }

    /// Called at app launch; never kills processes based on persisted PID numbers.
    func start() async throws {
        guard qwen3Process == nil else { return }
        Self.syncHotwordsFile()

        let qwen3Enabled = UserDefaults.standard.object(forKey: "tf_qwen3FinalEnabled") as? Bool ?? true

        DebugFileLogger.log("start(): q3=\(qwen3Enabled)")

        if qwen3Enabled && qwen3Process == nil {
            do {
                try await launchQwen3Server()
            } catch {
                logger.warning("Qwen3-ASR failed to start: \(error)")
                DebugFileLogger.log("Qwen3-ASR launch failed: \(error)")
                throw error
            }
        }

        DebugFileLogger.log("start() done: q3Port=\(Self.currentQwen3Port ?? -1)")
    }

    /// Launch the Qwen3-ASR server (final calibration + LLM).
    private func launchQwen3Server() async throws {
        launchGeneration &+= 1
        let generation = launchGeneration
        let proc = Process()
        var completed = false
        defer {
            if !completed, generation == launchGeneration, qwen3Process === proc || qwen3Process == nil {
                if proc.isRunning { proc.terminate() }
                Self.processRegistry.remove(proc)
                modelPreparation?.cancel()
                modelPreparation = nil
                qwen3Process = nil
                qwen3Port = nil
                Self.currentQwen3Port = nil
                qwen3PortReader?.cancel()
                qwen3PortReader = nil
                qwen3StdoutPipe = nil
            }
        }
        var args: [String] = []

        // Reserve this generation before model hashing suspends the actor.
        qwen3Process = proc
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let components = ASRComponentResolver.defaultRoot(applicationSupport: support)
        let preparation = Task.detached(priority: .userInitiated) {
            try ASRComponentResolver.managedModelIfPresent(in: components,
                modelID: QwenModelCatalog.modelID, manifest: QwenModelCatalog.manifest())
        }
        modelPreparation = preparation
        let managedModel = try await withTaskCancellationHandler {
            try await preparation.value
        } onCancel: { preparation.cancel() }
        guard generation == launchGeneration, qwen3Process === proc, !Task.isCancelled else {
            throw CancellationError()
        }
        modelPreparation = nil
        try configureQwen3Server(proc: proc, args: &args, managedModel: managedModel)
        if managedModel != nil {
            var environment = ProcessInfo.processInfo.environment
            environment["HF_HUB_OFFLINE"] = "1"
            environment["TRANSFORMERS_OFFLINE"] = "1"
            proc.environment = environment
        }

        proc.arguments = args

        let pipe = Pipe()
        proc.standardOutput = pipe
        // Third-party diagnostics can contain paths or input-derived content.
        // Keep native lifecycle diagnostics, but never persist raw helper stderr.
        // A null sink also avoids pipe backpressure without an orphaned handler.
        ASRProcessOutputPolicy.suppressRawDiagnostics(of: proc)
        self.qwen3StdoutPipe = pipe
        let portReader = ASRPortReader(pipe: pipe)
        self.qwen3PortReader = portReader

        proc.terminationHandler = { [weak self] exited in
            Task { await self?.handleProcessExit(exited, generation: generation) }
        }

        logger.info("Starting Qwen3-ASR server: \(proc.executableURL?.path ?? "?")")

        do {
            try proc.run()
        } catch {
            logger.error("Failed to start Qwen3-ASR server: \(error)")
            throw ServerError.launchFailed(error)
        }
        guard Self.processRegistry.insert(proc) else {
            if proc.isRunning { proc.terminate() }
            throw CancellationError()
        }
        self.qwen3Process = proc

        let portResult = await portReader.waitForPort(timeout: 120)
        if portResult == nil && !proc.isRunning {
            DebugFileLogger.log("Qwen3-ASR exited before port: status=\(proc.terminationStatus), reason=\(proc.terminationReason.rawValue); stderr suppressed")
        }
        guard generation == launchGeneration, qwen3Process === proc, proc.isRunning, !Task.isCancelled else {
            throw CancellationError()
        }
        guard let discoveredPort = portResult else {
            throw ServerError.portDiscoveryFailed
        }
        logger.info("Qwen3-ASR server started on port \(discoveredPort)")

        // Health check for Qwen3
        let qwen3HealthURL = URL(string: "http://127.0.0.1:\(discoveredPort)/health")!
        var healthy = false
        let deadline = ContinuousClock.now.advanced(by: .seconds(30))
        while ContinuousClock.now < deadline {
            guard generation == launchGeneration, qwen3Process === proc, proc.isRunning, !Task.isCancelled else { throw CancellationError() }
            do {
                var request = URLRequest(url: qwen3HealthURL)
                request.timeoutInterval = 2
                let (data, response) = try await URLSession.shared.data(for: request)
                guard generation == launchGeneration, qwen3Process === proc, proc.isRunning, !Task.isCancelled else { throw CancellationError() }
                if Self.isReadyHealth(status: (response as? HTTPURLResponse)?.statusCode ?? 0, data: data) {
                    healthy = true
                    break
                }
            } catch is CancellationError { throw CancellationError() }
            catch {}
            try await Task.sleep(for: .milliseconds(200))
        }
        if !healthy {
            logger.error("Qwen3-ASR server started but health check failed after 30s")
            DebugFileLogger.log("Qwen3-ASR health check failed — server may be non-functional")
            throw ServerError.launchFailed(
                NSError(domain: "SenseVoiceServerManager", code: -1,
                        userInfo: [NSLocalizedDescriptionKey: "Health check failed after server start"])
            )
        }
        self.qwen3Port = discoveredPort
        Self.currentQwen3Port = discoveredPort
        completed = true
    }

    /// An old child's delayed exit must never clear a newer startup generation.
    private func handleProcessExit(_ exited: Process, generation: UInt64) {
        Self.processRegistry.remove(exited)
        guard generation == launchGeneration, qwen3Process === exited else { return }
        launchGeneration &+= 1
        modelPreparation?.cancel()
        modelPreparation = nil
        qwen3Process = nil
        qwen3Port = nil
        Self.currentQwen3Port = nil
        qwen3PortReader?.cancel()
        qwen3PortReader = nil
        qwen3StdoutPipe = nil
        DebugFileLogger.log("Qwen3-ASR process exited: status=\(exited.terminationStatus), reason=\(exited.terminationReason.rawValue); stderr suppressed")
    }

    /// Start the Qwen3-ASR server independently.
    func startQwen3() async throws {
        guard qwen3Process == nil else { return }
        try await launchQwen3Server()
    }

    /// Stop the Qwen3-ASR server independently (e.g. when user disables verification).
    func stopQwen3() {
        launchGeneration &+= 1
        modelPreparation?.cancel()
        modelPreparation = nil
        if let proc = qwen3Process {
            if proc.isRunning { proc.terminate() }
            Self.processRegistry.remove(proc)
        }
        qwen3Process = nil
        qwen3Port = nil
        Self.currentQwen3Port = nil
        qwen3PortReader?.cancel()
        qwen3PortReader = nil
        qwen3StdoutPipe = nil
        logger.info("Qwen3-ASR server stopped")
        DebugFileLogger.log("Qwen3-ASR server stopped (user toggle)")
    }

    /// Stop the server.
    func stop() {
        launchGeneration &+= 1
        modelPreparation?.cancel()
        modelPreparation = nil
        if let proc = qwen3Process {
            if proc.isRunning { proc.terminate() }
            Self.processRegistry.remove(proc)
        }
        qwen3Process = nil
        qwen3Port = nil
        Self.currentQwen3Port = nil
        qwen3PortReader?.cancel()
        qwen3PortReader = nil
        qwen3StdoutPipe = nil

        logger.info("Qwen3-ASR server stopped")
    }

    /// Check only the current generation; an old response cannot validate a restarted child.
    func isHealthy() async -> Bool {
        await probeHealth { request in
            let (data, response) = try await URLSession.shared.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            return Self.isReadyHealth(status: status, data: data) ? 200 : 503
        }
    }

    /// HTTP success alone is not readiness: require the helper's loaded-model contract.
    nonisolated private static func isReadyHealth(status: Int, data: Data) -> Bool {
        struct Health: Decodable {
            let status: String
            let model_loaded: Bool
        }
        guard status == 200, data.count <= 4096,
              let health = try? JSONDecoder().decode(Health.self, from: data) else { return false }
        return health.status == "ok" && health.model_loaded
    }

    private func probeHealth(using probe: @Sendable (URLRequest) async throws -> Int) async -> Bool {
        guard let port = qwen3Port, let process = qwen3Process else { return false }
        let generation = launchGeneration
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/health")!)
        request.timeoutInterval = 2
        do {
            let status = try await probe(request)
            guard !Task.isCancelled, generation == launchGeneration,
                  qwen3Process === process, process.isRunning, qwen3Port == port else { return false }
            return status == 200
        } catch {
            return false
        }
    }

    // MARK: - Qwen3-ASR

    private func configureQwen3Server(proc: Process, args: inout [String], managedModel: URL?) throws {
        let (executable, serverScript) = try Self.resolveCurrentServerLaunch()

        // Model path: bundled or ModelScope cache
        guard let modelPath = managedModel?.path ?? resolveQwen3ModelPath() else {
            throw ServerError.modelNotFound
        }
        logger.info("Qwen3-ASR model: \(modelPath)")

        // Hotwords file
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let hotwordsPath = appSupport
            .appendingPathComponent("VoxLog")
            .appendingPathComponent("hotwords.txt")
        let hotwordsFile = FileManager.default.fileExists(atPath: hotwordsPath.path) ? hotwordsPath.path : ""

        proc.executableURL = URL(fileURLWithPath: executable)
        if !serverScript.isEmpty {
            args.append(serverScript)
        }
        args += [
            "--model-path", modelPath,
            "--port", "0",
            "--hotwords-file", hotwordsFile,
        ]
        logger.info("Starting Qwen3-ASR server")
    }

    /// Shared by settings availability and launch; release cannot discover developer runtimes.
    nonisolated static func resolveCurrentServerLaunch(home: String = NSHomeDirectory()) throws
        -> (executable: String, script: String) {
        #if DEBUG
        let devDir = findDevServerDir(name: "qwen3-asr-server", home: home)
        #else
        let devDir: String? = nil
        #endif
        let bundled = Bundle.main.executableURL?.deletingLastPathComponent()
            .appendingPathComponent("qwen3-asr-server").path
        #if DEBUG
        let candidates = (devDir.map { [$0 + "/.venv/bin/python"] } ?? [])
            + ModelManager.qwen3PythonPaths(home: home)
        #else
        let candidates: [String] = []
        #endif
        return try resolveServerLaunch(
            bundledBinary: bundled, devDirectory: devDir,
            pythonCandidates: candidates
        )
    }

    nonisolated static func resolveServerLaunch(
        bundledBinary: String?, devDirectory: String?, pythonCandidates: [String]
    ) throws -> (executable: String, script: String) {
        let fm = FileManager.default
        func isExecutableFile(_ path: String) -> Bool {
            var directory: ObjCBool = false
            return fm.fileExists(atPath: path, isDirectory: &directory)
                && !directory.boolValue && fm.isExecutableFile(atPath: path)
        }
        // Installed offline apps must use their bundled runtime, even when an
        // older developer checkout happens to exist on this machine.
        if let bundledBinary, isExecutableFile(bundledBinary) {
            return (bundledBinary, "")
        }
        // Release builds must fail closed if the packaged helper is missing.
        #if DEBUG
        if let devDirectory {
            guard let python = pythonCandidates.first(where: isExecutableFile) else {
                throw ServerError.venvNotFound
            }
            return (python, devDirectory + "/server.py")
        }
        #endif
        throw ServerError.serverNotFound
    }

    private func resolveQwen3ModelPath() -> String? {
        ModelManager.legacyQwenModel()?.path
    }

    // MARK: - Dev server discovery

    nonisolated private static func findDevServerDir(name: String, home: String) -> String? {
        // Walk up from binary location to find server directory
        var dir = Bundle.main.bundlePath
        for _ in 0..<5 {
            dir = (dir as NSString).deletingLastPathComponent
            let candidate = (dir as NSString).appendingPathComponent(name)
            if FileManager.default.fileExists(atPath: (candidate as NSString).appendingPathComponent("server.py")) {
                return candidate
            }
        }
        // Check app bundle Resources
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent(name).path,
           FileManager.default.fileExists(atPath: (bundled as NSString).appendingPathComponent("server.py")) {
            return bundled
        }

        guard name == "qwen3-asr-server" else { return nil }
        for path in ModelManager.qwen3ServerDirectories(home: home) {
            if FileManager.default.fileExists(atPath: (path as NSString).appendingPathComponent("server.py")) {
                return path
            }
        }
        return nil
    }

    // MARK: - Errors

    enum ServerError: Error, LocalizedError {
        case serverNotFound
        case venvNotFound
        case modelNotFound
        case launchFailed(Error)
        case portDiscoveryFailed

        var errorDescription: String? {
            switch self {
            case .serverNotFound:
                return L("Qwen3-ASR 服务未找到", "Qwen3-ASR server not found")
            case .venvNotFound:
                return L("Python 环境未配置", "Python environment not configured")
            case .modelNotFound:
                return L("本地 ASR 模型未找到，请先下载", "Local ASR model not found, please download first")
            case .launchFailed(let e):
                return L("服务启动失败: \(e.localizedDescription)", "Server launch failed: \(e.localizedDescription)")
            case .portDiscoveryFailed:
                return L("服务端口发现失败", "Server port discovery failed")
            }
        }
    }
}
