#if ASR_EXIT_HARNESS
// Appended to an unchanged copy of the production manager by the harness builder.
// Same-file extension permits testing private actor state without shipping a test API.
extension SenseVoiceServerManager {
    private func mutateHealth(_ field: String) {
        switch field {
        case "generation": launchGeneration &+= 1
        case "process": qwen3Process = Process()
        case "port": qwen3Port = 12346
        default: fatalError("invalid fixture mutation")
        }
    }

    nonisolated static func exerciseHealthPayloads() {
        let ready = #"{"status":"ok","model_loaded":true}"#
        let cases: [(Int, String, Bool)] = [
            (200, #"{"status":"ok","model_loaded":true}"#, true),
            (200, #"{"status":"ok","model_loaded":true,"llm_loaded":false}"#, true),
            (503, #"{"status":"ok","model_loaded":true}"#, false),
            (200, #"{"status":"ok","model_loaded":false}"#, false),
            (200, #"{"status":"starting","model_loaded":true}"#, false),
            (200, #"{"status":"ok"}"#, false),
            (200, #"{"status":"ok","model_loaded":"true"}"#, false),
            (200, #"{"status":"ok","model_loaded":1}"#, false),
            (200, #"{"status":"ok","model_loaded":null}"#, false),
            (200, "[]", false), (200, "<html>ok</html>", false), (200, "", false),
            (200, ready + String(repeating: " ", count: 4096 - ready.utf8.count), true),
            (200, ready + String(repeating: " ", count: 4097 - ready.utf8.count), false),
        ]
        for (status, body, expected) in cases {
            precondition(isReadyHealth(status: status, data: Data(body.utf8)) == expected)
        }
        print("ASR health payload: 14 readiness contract cases passed")
    }

    func exerciseHealth(_ child: Process) async {
        launchGeneration = 42
        qwen3Process = child
        qwen3Port = nil
        let absent = await probeHealth { _ in fatalError("absent service must not probe") }
        precondition(!absent)
        qwen3Port = 12345
        let healthy = await probeHealth { request in
            precondition(request.timeoutInterval == 2)
            precondition(request.url?.absoluteString == "http://127.0.0.1:12345/health")
            return 200
        }
        precondition(healthy)
        let unavailable = await probeHealth { _ in 503 }
        precondition(!unavailable)
        let failed = await probeHealth { _ in throw URLError(.timedOut) }
        precondition(!failed)
        for field in ["generation", "process", "port"] {
            launchGeneration = 42
            qwen3Process = child
            qwen3Port = 12345
            let changed = await probeHealth { _ in
                await self.mutateHealth(field)
                return 200
            }
            precondition(!changed)
        }
        qwen3Process = child
        qwen3Port = 12345
        let cancelled = await Task {
            await self.probeHealth { _ in
                withUnsafeCurrentTask { $0?.cancel() }
                return 200
            }
        }.value
        precondition(!cancelled)
        let stale = await probeHealth { _ in
            await self.stop()
            return 200
        }
        precondition(!stale)
        child.waitUntilExit()
        qwen3Process = child
        qwen3Port = 12345
        let dead = await probeHealth { _ in 200 }
        precondition(!dead)
        qwen3Process = nil
        qwen3Port = nil
    }

    func exerciseExit(_ old: Process, replacement: Process, generation: UInt64) {
        launchGeneration = generation
        qwen3Process = replacement
        qwen3Port = 12345
        Self.currentQwen3Port = 12345
        handleProcessExit(old, generation: generation)
        precondition(qwen3Process === replacement && qwen3Port == 12345)
        handleProcessExit(replacement, generation: generation &- 1)
        precondition(qwen3Process === replacement && Self.currentQwen3Port == 12345)
        handleProcessExit(replacement, generation: generation)
        precondition(qwen3Process == nil && qwen3Port == nil && Self.currentQwen3Port == nil)
        precondition(launchGeneration == generation &+ 1)
        // Duplicate callback stays harmless.
        handleProcessExit(replacement, generation: generation)
        precondition(launchGeneration == generation &+ 1)
    }
}
@main enum ASRExitHarness {
    static func main() async throws {
        func exitedChild() throws -> Process {
            let child = Process()
            child.executableURL = URL(fileURLWithPath: "/usr/bin/true")
            try child.run()
            child.waitUntilExit()
            return child
        }
        let old = try exitedChild()
        let replacement = try exitedChild()
        let manager = SenseVoiceServerManager()
        for generation in 1...1000 {
            await manager.exerciseExit(old, replacement: replacement, generation: UInt64(generation))
        }
        let healthChild = Process()
        healthChild.executableURL = URL(fileURLWithPath: "/bin/sleep")
        healthChild.arguments = ["10"]
        try healthChild.run()
        defer { if healthChild.isRunning { healthChild.terminate() } }
        SenseVoiceServerManager.exerciseHealthPayloads()
        await manager.exerciseHealth(healthChild)
        print("ASR health: 12 request and stale-response assertions passed")
        print("ASR exit actor: 1000 deterministic sequences / 5000 assertions passed")
    }
}
#endif
