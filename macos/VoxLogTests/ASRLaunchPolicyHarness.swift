#if ASR_LAUNCH_POLICY_HARNESS
import Foundation

@main
struct ASRLaunchPolicyHarness {
    static func main() throws {
        let bundled = try SenseVoiceServerManager.resolveServerLaunch(
            bundledBinary: "/usr/bin/true", devDirectory: "/stale/checkout",
            pythonCandidates: ["/usr/bin/false"])
        precondition(bundled.executable == "/usr/bin/true" && bundled.script.isEmpty)
        for bundle in [nil, "/nonexistent/vox-test-helper"] as [String?] {
            #if DEBUG
            let fallback = try SenseVoiceServerManager.resolveServerLaunch(
                bundledBinary: bundle, devDirectory: "/dev/server",
                pythonCandidates: ["/nonexistent/python", "/usr/bin/true"])
            precondition(fallback.executable == "/usr/bin/true")
            precondition(fallback.script == "/dev/server/server.py")
            #else
            do {
                _ = try SenseVoiceServerManager.resolveServerLaunch(
                    bundledBinary: bundle, devDirectory: "/dev/server",
                    pythonCandidates: ["/usr/bin/true"])
                fatalError("Release must not fall back to developer Python")
            } catch SenseVoiceServerManager.ServerError.serverNotFound { }
            #endif
        }
        do {
            _ = try SenseVoiceServerManager.resolveServerLaunch(
                bundledBinary: nil, devDirectory: nil, pythonCandidates: ["/usr/bin/true"])
            fatalError("Missing helper must fail")
        } catch SenseVoiceServerManager.ServerError.serverNotFound { }
        // Settings and startup resolve the same real bundle/developer fixture.
        let fm = FileManager.default
        let home = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try fm.createDirectory(at: home, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: home) }
        let script = home.appendingPathComponent("voxlog/macos/qwen3-asr-server/server.py")
        let python = home.appendingPathComponent(".voxlog-venv/bin/python")
        for file in [script, python] {
            try fm.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data("fixture, never executed".utf8).write(to: file)
        }
        try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: python.path)
        #if DEBUG
        precondition(ModelManager.isQwen3ASRServerAvailable(home: home.path))
        let developer = try SenseVoiceServerManager.resolveCurrentServerLaunch(home: home.path)
        precondition(developer.executable == python.path && developer.script == script.path)
        #else
        precondition(!ModelManager.isQwen3ASRServerAvailable(home: home.path))
        #endif
        let helper = Bundle.main.executableURL!.deletingLastPathComponent()
            .appendingPathComponent("qwen3-asr-server")
        precondition(!fm.fileExists(atPath: helper.path), "Never overwrite an existing helper")
        try fm.createDirectory(at: helper, withIntermediateDirectories: false)
        do {
            _ = try SenseVoiceServerManager.resolveServerLaunch(
                bundledBinary: helper.path, devDirectory: nil, pythonCandidates: [])
            fatalError("Executable directory must not count as a helper")
        } catch SenseVoiceServerManager.ServerError.serverNotFound { }
        try fm.removeItem(at: helper)
        try Data("fixture, never executed".utf8).write(to: helper)
        defer { try? fm.removeItem(at: helper) }
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: helper.path)
        #if !DEBUG
        precondition(!ModelManager.isQwen3ASRServerAvailable(home: home.path))
        #endif
        try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helper.path)
        precondition(ModelManager.isQwen3ASRServerAvailable(home: home.path))
        let current = try SenseVoiceServerManager.resolveCurrentServerLaunch(home: home.path)
        precondition(current.executable == helper.path && current.script.isEmpty)
        precondition(ModelManager.isQwen3ASRBundled, "Bundled runtime preflight must not require a legacy model")
        precondition(QwenModelCatalog.revision.count == 40 && QwenModelCatalog.revision.allSatisfy(\.isHexDigit))
        // Legacy discovery has explicit precedence and revision, but does not claim byte trust.
        let resources = home.appendingPathComponent("Resources")
        let support = home.appendingPathComponent("Support")
        func legacy() -> URL? {
            ASRComponentResolver.legacyQwenModel(bundleResources: resources,
                applicationSupport: support, home: home)
        }
        func weight(_ directory: URL) throws {
            try fm.createDirectory(at: directory, withIntermediateDirectories: true)
            try Data().write(to: directory.appendingPathComponent("model.safetensors"))
        }
        let snapshots = home.appendingPathComponent(".cache/huggingface/hub/models--Qwen--Qwen3-ASR-0.6B/snapshots")
        try weight(snapshots.appendingPathComponent("zzzz-other-revision"))
        try weight(home.appendingPathComponent(".cache/modelscope/hub/models/Qwen/Qwen3-ASR-1.7B"))
        precondition(legacy() == nil)
        let ms = home.appendingPathComponent(".cache/modelscope/hub/models/Qwen/Qwen3-ASR-0.6B")
        try weight(ms)
        precondition(legacy()?.path == ms.path)
        let pinned = snapshots.appendingPathComponent(QwenModelCatalog.revision)
        try weight(pinned)
        precondition(legacy()?.path == pinned.path)
        let installed = support.appendingPathComponent("VoxLog/Models/Qwen3-ASR")
        try fm.createDirectory(at: installed.appendingPathComponent("model.safetensors"), withIntermediateDirectories: true)
        precondition(legacy()?.path == pinned.path, "A directory is not a weights file")
        try fm.removeItem(at: installed.appendingPathComponent("model.safetensors"))
        try weight(installed)
        precondition(legacy()?.path == installed.path)
        let bundledModel = resources.appendingPathComponent("Models/Qwen3-ASR")
        try fm.createDirectory(at: bundledModel, withIntermediateDirectories: true)
        precondition(legacy()?.path == installed.path, "Empty bundle directory is not a model")
        try weight(bundledModel)
        precondition(legacy()?.path == bundledModel.path)
        print("Legacy selection: wrong revision/1.7B rejected, fixed precedence and missing weights checked")
        #if DEBUG
        print("Debug launch policy: bundled priority, missing/invalid bundle fallback and missing helper passed")
        #else
        print("Release launch policy: bundled priority, missing/invalid bundle rejection and missing helper passed")
        #endif
    }
}
#endif
