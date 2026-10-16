import Foundation

/// Manages the Python Agent Engine process lifecycle.
///
/// Responsibilities:
/// - Start/stop the Python engine process
/// - Health check polling (every 5s)
/// - Auto-restart on crash (max 3 times)
/// - Coordinate WebSocket connection
///
/// Architecture:
/// ```
/// EngineCoordinator
///   ├── Process (Python engine.server)
///   ├── EngineWebSocket (ws://127.0.0.1:{port}/ws)
///   └── EngineEventHandler (event → UI state)
/// ```
@Observable
final class EngineCoordinator {

    enum EngineState: String {
        case stopped
        case starting
        case running
        case error
    }

    // MARK: - Observable State

    var state: EngineState = .stopped
    var port: Int = 0
    private(set) var lastError: String?
    private(set) var restartCount: Int = 0

    let websocket = EngineWebSocket()
    let eventHandler = EngineEventHandler()
    private let operationsClient = EngineOperationsClient()

    // MARK: - Private

    private var process: Process?
    private var healthTimer: Timer?
    private let stderrLock = NSLock()
    private var engineStderr = Data()
    private let maxRestarts = 3
    private let maxCapturedStderrBytes = 8 * 1024
    private let healthInterval: TimeInterval = 5.0
    private let readinessInterval: TimeInterval = 0.5
    private let maxReadinessAttempts = 60

    init() {
        eventHandler.approvalSender = websocket
    }

    // MARK: - Lifecycle

    /// Start the Python engine process and connect WebSocket.
    func start() {
        guard state == .stopped || state == .error else { return }
        state = .starting

        // Find a free port
        port = findFreePort()

        // Wire up event handler
        websocket.onEvent = { [weak self] event in
            self?.eventHandler.handle(event)
        }

        let proc = Process()
        let resourceURL = Bundle.main.resourceURL
            ?? Bundle.main.bundleURL.appendingPathComponent("Contents/Resources")

        if let bundledEngine = Self.resolveBundledEngineExecutable(resourceURL: resourceURL) {
            proc.executableURL = bundledEngine
            proc.arguments = [
                "--host", "127.0.0.1",
                "--port", "\(port)",
                "--log-level", "info",
            ]
            proc.currentDirectoryURL = resourceURL
            NSLog("[VoxAgents] Using bundled engine: %@", bundledEngine.path)
        } else if let workDir = engineWorkingDirectory() {
            let venvPython = workDir.appendingPathComponent(".venv/bin/python3")
            if FileManager.default.isExecutableFile(atPath: venvPython.path) {
                proc.executableURL = venvPython
                proc.arguments = [
                    "-m", "uvicorn", "engine.server:app",
                    "--host", "127.0.0.1",
                    "--port", "\(port)",
                    "--log-level", "info",
                ]
                NSLog("[VoxAgents] Using repository venv: %@", venvPython.path)
            } else {
                proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
                proc.arguments = [
                    "python3", "-m", "uvicorn", "engine.server:app",
                    "--host", "127.0.0.1",
                    "--port", "\(port)",
                    "--log-level", "info",
                ]
                NSLog("[VoxAgents] Using system Python for source checkout")
            }
            proc.currentDirectoryURL = workDir
        } else {
            state = .error
            lastError = "Vox Agent engine runtime is missing. Reinstall the application."
            return
        }
        let translationConfig = KeychainService.selectedLLMProvider == .claude
            ? nil
            : KeychainService.loadLLMConfig()
        proc.environment = Self.engineEnvironment(
            base: ProcessInfo.processInfo.environment,
            translationConfig: translationConfig
        )

        // Capture stderr for debugging
        let errPipe = Pipe()
        proc.standardError = errPipe
        resetEngineStderr()
        errPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else {
                handle.readabilityHandler = nil
                return
            }
            self?.appendEngineStderr(data)
        }

        proc.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                guard let self,
                      self.process === p,
                      self.state == .starting || self.state == .running else { return }
                self.process = nil
                self.state = .error
                let detail = Self.safeEngineFailureSummary(self.capturedEngineStderr())
                let message = detail.isEmpty
                    ? "engine process exited with code \(p.terminationStatus)"
                    : "engine process exited with code \(p.terminationStatus): \(detail)"
                self.lastError = message
                DebugFileLogger.log(message)
                NSLog("[VoxAgents] %@", message)
                self.websocket.disconnect()
                self.scheduleRestart()
            }
        }

        do {
            try proc.run()
            process = proc
            waitForReadiness(process: proc)
        } catch {
            state = .error
            lastError = error.localizedDescription
        }
    }

    /// Stop the engine process and disconnect.
    func stop() {
        healthTimer?.invalidate()
        healthTimer = nil
        websocket.disconnect()
        process?.terminate()
        process = nil
        state = .stopped
        restartCount = 0
    }

    /// Fetch the server-authoritative Agent roster, tasks, and pending approvals.
    func fetchOperations() async throws -> EngineOperationsSnapshot {
        guard state == .running, port > 0 else {
            throw EngineOperationsClientError.engineUnavailable
        }
        return try await operationsClient.fetchSnapshot(port: port)
    }

    /// Route a user command through the canonical engine orchestrator.
    func sendCommand(
        text: String,
        agentId: String?,
        provider: String = "claude",
        workingDirectory: String? = nil
    ) async throws -> EngineCommandResponse {
        guard state == .running, port > 0 else {
            throw EngineOperationsClientError.engineUnavailable
        }
        return try await operationsClient.sendCommand(
            port: port,
            text: text,
            agentId: agentId,
            provider: provider,
            workingDirectory: workingDirectory
        )
    }

    // MARK: - Health Check

    private func waitForReadiness(process candidate: Process, attempt: Int = 0) {
        guard process === candidate, state == .starting else { return }
        guard let url = URL(string: "http://127.0.0.1:\(port)/health") else { return }

        var request = URLRequest(url: url)
        request.timeoutInterval = 1.0
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self,
                      self.process === candidate,
                      self.state == .starting else { return }

                if Self.isHealthyResponse(data: data, response: response, error: error) {
                    self.state = .running
                    self.lastError = nil
                    self.websocket.connect(port: self.port)
                    self.startHealthCheck()
                    return
                }

                guard candidate.isRunning else { return }
                let nextAttempt = attempt + 1
                guard nextAttempt < self.maxReadinessAttempts else {
                    self.state = .error
                    self.lastError = "Vox Agent engine did not become ready in time"
                    self.process = nil
                    candidate.terminate()
                    self.scheduleRestart()
                    return
                }

                DispatchQueue.main.asyncAfter(deadline: .now() + self.readinessInterval) {
                    self.waitForReadiness(process: candidate, attempt: nextAttempt)
                }
            }
        }.resume()
    }

    static func isHealthyResponse(
        data: Data?,
        response: URLResponse?,
        error: Error?
    ) -> Bool {
        guard error == nil,
              let http = response as? HTTPURLResponse,
              http.statusCode == 200,
              let data,
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return false }
        return payload["status"] as? String == "ok"
    }

    private func startHealthCheck() {
        healthTimer = Timer.scheduledTimer(withTimeInterval: healthInterval, repeats: true) { [weak self] _ in
            self?.checkHealth()
        }
    }

    private func checkHealth() {
        guard state == .running else { return }
        guard let url = URL(string: "http://127.0.0.1:\(port)/health") else { return }

        var request = URLRequest(url: url)
        request.timeoutInterval = 3.0

        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                if let error = error {
                    self?.lastError = "health check failed: \(error.localizedDescription)"
                    // don't change state — let terminationHandler handle crash
                }
            }
        }.resume()
    }

    // MARK: - Auto Restart

    private func scheduleRestart() {
        guard restartCount < maxRestarts else {
            lastError = "engine crashed \(maxRestarts) times, giving up"
            state = .error
            return
        }
        restartCount += 1
        let delay = Double(restartCount) * 2.0
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.start()
        }
    }

    // MARK: - Helpers

    private func findFreePort() -> Int {
        // Start from a deterministic base, try until we find one
        let base = 18900
        for offset in 0..<100 {
            let p = base + offset
            let socket = socket(AF_INET, SOCK_STREAM, 0)
            guard socket >= 0 else { continue }
            defer { close(socket) }

            var addr = sockaddr_in()
            addr.sin_family = sa_family_t(AF_INET)
            addr.sin_port = in_port_t(p).bigEndian
            addr.sin_addr.s_addr = inet_addr("127.0.0.1")

            let result = withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    bind(socket, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            if result == 0 { return p }
        }
        return 18900
    }

    private func engineWorkingDirectory() -> URL? {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let sourceRepositoryRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        var candidates: [URL] = []
        if let override = ProcessInfo.processInfo.environment["VOXAGENT_ENGINE_DIR"] {
            candidates.append(URL(fileURLWithPath: override).standardizedFileURL)
        }
        candidates.append(contentsOf: [
            currentDirectory,
            currentDirectory.deletingLastPathComponent(),
            sourceRepositoryRoot,
            URL(fileURLWithPath: NSHomeDirectory())
                .appendingPathComponent("Developer/ClaudeCode/01_active/voxAgent"),
        ])

        guard let resolved = Self.resolveEngineWorkingDirectory(candidates: candidates) else {
            NSLog("[VoxAgents] Engine source checkout not found")
            return nil
        }
        NSLog("[VoxAgents] Engine source dir: %@", resolved.path)
        return resolved
    }

    static func resolveEngineWorkingDirectory(candidates: [URL]) -> URL? {
        for candidate in candidates {
            let engineInit = candidate.appendingPathComponent("engine/__init__.py")
            if FileManager.default.fileExists(atPath: engineInit.path) {
                return candidate
            }
        }
        return nil
    }

    static func resolveBundledEngineExecutable(resourceURL: URL) -> URL? {
        let executable = resourceURL.appendingPathComponent("bin/voxagent-engine")
        return FileManager.default.isExecutableFile(atPath: executable.path)
            ? executable
            : nil
    }

    static func engineEnvironment(
        base: [String: String],
        translationConfig: LLMConfig?
    ) -> [String: String] {
        guard let translationConfig,
              !translationConfig.model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !translationConfig.baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return base }

        var environment = base
        environment["VOXAGENT_TRANSLATION_BASE_URL"] = translationConfig.baseURL
        environment["VOXAGENT_TRANSLATION_MODEL"] = translationConfig.model
        environment["VOXAGENT_TRANSLATION_API_KEY"] = translationConfig.apiKey
        return environment
    }

    static func safeEngineFailureSummary(_ data: Data, limit: Int = 512) -> String {
        guard limit > 0, var value = String(data: data, encoding: .utf8) else { return "" }
        let patterns = [
            #"(?i)(authorization\s*:\s*bearer\s+)\S+"#,
            #"(?i)((?:VOXAGENT_[A-Z0-9_]*API_KEY|api[_-]?key)\s*[=:]\s*)\S+"#,
        ]
        for pattern in patterns {
            guard let expression = try? NSRegularExpression(pattern: pattern) else { continue }
            let range = NSRange(value.startIndex..<value.endIndex, in: value)
            value = expression.stringByReplacingMatches(
                in: value,
                range: range,
                withTemplate: "$1[REDACTED]"
            )
        }
        value = value
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
        return String(value.suffix(limit))
    }

    private func resetEngineStderr() {
        stderrLock.lock()
        engineStderr.removeAll(keepingCapacity: true)
        stderrLock.unlock()
    }

    private func appendEngineStderr(_ data: Data) {
        stderrLock.lock()
        engineStderr.append(data)
        if engineStderr.count > maxCapturedStderrBytes {
            engineStderr = engineStderr.suffix(maxCapturedStderrBytes)
        }
        stderrLock.unlock()
    }

    private func capturedEngineStderr() -> Data {
        stderrLock.lock()
        defer { stderrLock.unlock() }
        return engineStderr
    }
}
