import Foundation
import Darwin

/// Explicit revision selection. No receipt shortcuts, snapshot guessing, download or fallback.
/// Call preparation on a worker task: hashing weights must not block the UI actor.
enum ASRComponentResolver {
    enum Failure: Error { case missingModel }

    static func defaultRoot(applicationSupport: URL) -> URL {
        applicationSupport.appendingPathComponent("VoxLog/Components", isDirectory: true)
    }

    /// Missing store means legacy installation; an existing broken store must never fall back.
    static func managedModelIfPresent(in components: URL, modelID: String,
                                      manifest: ModelManifest) throws -> URL? {
        var info = stat()
        if lstat(components.path, &info) != 0 {
            if errno == ENOENT { return nil }
            throw ModelTreeVerifier.Failure.unsafeRoot
        }
        let fd = try ModelTreeVerifier.openRoot(components)
        close(fd)
        return try verifiedModel(in: components, modelID: modelID, manifest: manifest)
    }

    /// Compatibility discovery only: existence is not hash verification.
    /// Select the declared 0.6B snapshot, never guess a revision or substitute 1.7B.
    static func legacyQwenModel(bundleResources: URL?, applicationSupport: URL, home: URL) -> URL? {
        var candidates: [URL] = []
        if let bundleResources {
            candidates.append(bundleResources.appendingPathComponent("Models/Qwen3-ASR"))
        }
        candidates.append(applicationSupport.appendingPathComponent("VoxLog/Models/Qwen3-ASR"))
        candidates.append(home.appendingPathComponent(
            ".cache/huggingface/hub/models--Qwen--Qwen3-ASR-0.6B/snapshots/" + QwenModelCatalog.revision))
        candidates.append(home.appendingPathComponent(".cache/modelscope/hub/models/Qwen/Qwen3-ASR-0.6B"))
        return candidates.first { candidate in
            var isDirectory: ObjCBool = false
            let weights = candidate.appendingPathComponent("model.safetensors").path
            return FileManager.default.fileExists(atPath: weights, isDirectory: &isDirectory)
                && !isDirectory.boolValue
        }
    }

    static func qwenModel(in components: URL) throws -> URL {
        try verifiedModel(in: components, modelID: QwenModelCatalog.modelID,
                          manifest: QwenModelCatalog.manifest())
    }

    static func verifiedModel(in components: URL, modelID: String, manifest: ModelManifest) throws -> URL {
        guard ModelStore.validModelID(modelID) else { throw ModelStore.Failure.invalidModelID }
        let path = components.appendingPathComponent("models", isDirectory: true)
            .appendingPathComponent(modelID, isDirectory: true)
            .appendingPathComponent(manifest.digest, isDirectory: true)
        guard FileManager.default.fileExists(atPath: path.path) else { throw Failure.missingModel }
        try ModelTreeVerifier.verify(root: path, manifest: manifest)
        return path
    }
}
