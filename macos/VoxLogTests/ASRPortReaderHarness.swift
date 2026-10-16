#if ASR_PORT_READER_HARNESS
import Foundation

@main enum ASRPortReaderHarness {
    static func main() async throws {
        var checks = 0
        func run(_ command: String, expected: Int?) async throws {
            let pipe = Pipe()
            let reader = ASRPortReader(pipe: pipe)
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/sh")
            process.arguments = ["-c", command]
            process.standardOutput = pipe
            ASRProcessOutputPolicy.suppressRawDiagnostics(of: process)
            defer { reader.cancel(); if process.isRunning { process.terminate() } }
            try process.run()
            let port = await reader.waitForPort(timeout: 1)
            precondition(port == expected)
            let deadline = ContinuousClock.now.advanced(by: .seconds(3))
            while process.isRunning && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
            precondition(!process.isRunning, "stdout pump stopped after port and blocked writer")
            checks += 1
        }
        try await run("printf 'PO'; sleep 0.02; printf 'RT:12345\\n'; dd if=/dev/zero bs=65536 count=32 2>/dev/null", expected: 12345)
        try await run("printf 'PORT:0\\nPORT:65536\\nPORT:-4\\nPORT:1234\\r\\n'", expected: 1234)
        try await run("printf 'not a port\\n'", expected: nil)
        try await run("dd if=/dev/zero bs=8192 count=1 2>/dev/null; printf '\\nPORT:3456\\n'", expected: 3456)
        try await run("printf 'PORT:65535\\n'; sleep 1.2; dd if=/dev/zero bs=65536 count=32 2>/dev/null", expected: 65535)
        try await run("printf 'PORT:123'", expected: nil)
        try await run("dd if=/dev/zero bs=65536 count=32 >&2; printf 'PORT:23456\\n'", expected: 23456)
        let pipe = Pipe()
        let reader = ASRPortReader(pipe: pipe)
        let task = Task { await reader.waitForPort(timeout: 10) }
        task.cancel()
        let canceled = await task.value
        precondition(canceled == nil); checks += 1
        reader.cancel()
        let timeoutPipe = Pipe()
        let timeoutReader = ASRPortReader(pipe: timeoutPipe)
        let timedOut = await timeoutReader.waitForPort(timeout: 0.02)
        precondition(timedOut == nil); checks += 1
        timeoutReader.cancel()
        func sleeper() throws -> Process {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/sleep")
            process.arguments = ["10"]
            try process.run()
            return process
        }
        let registry = ASRProcessRegistry()
        let managed = try sleeper()
        let unrelated = try sleeper()
        defer {
            if managed.isRunning { managed.terminate() }
            if unrelated.isRunning { unrelated.terminate() }
        }
        precondition(registry.insert(managed))
        registry.terminateAll()
        let stopDeadline = ContinuousClock.now.advanced(by: .seconds(2))
        while managed.isRunning && ContinuousClock.now < stopDeadline { try await Task.sleep(for: .milliseconds(10)) }
        precondition(!managed.isRunning && unrelated.isRunning); checks += 1
        precondition(!registry.insert(unrelated)); checks += 1
        let removedRegistry = ASRProcessRegistry()
        precondition(removedRegistry.insert(unrelated))
        removedRegistry.remove(unrelated)
        removedRegistry.terminateAll()
        precondition(unrelated.isRunning); checks += 1
        print("ASR stdout lifecycle: \(checks) checks passed")
    }
}
#endif
