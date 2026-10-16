import Darwin
import Foundation

/// Explicit offline import only. First installation is published atomically; failed imports never shadow legacy models.
enum OfflineModelImporter {
    enum Failure: Error { case unsafeStore, busy, installed }

    static func importQwen(source: URL, applicationSupport: URL) throws -> URL {
        try importModel(source: source, applicationSupport: applicationSupport,
                        modelID: QwenModelCatalog.modelID, manifest: QwenModelCatalog.manifest())
    }

    static func importModel(source: URL, applicationSupport: URL, modelID: String, manifest: ModelManifest) throws -> URL {
        try Task.checkCancellation()
        let base = try ModelTreeVerifier.openRoot(applicationSupport)
        defer { close(base) }
        if mkdirat(base, "VoxLog", 0o700) != 0 && errno != EEXIST { throw Failure.unsafeStore }
        let parent = openat(base, "VoxLog", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard parent >= 0 else { throw Failure.unsafeStore }
        defer { close(parent) }
        var info = stat()
        guard fstat(parent, &info) == 0, info.st_uid == geteuid(), info.st_mode & 0o022 == 0,
              fsync(base) == 0 else { throw Failure.unsafeStore }
        let writer = openat(parent, ".offline-import.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
        guard writer >= 0 else { throw Failure.unsafeStore }
        defer { close(writer) }
        guard fstat(writer, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_uid == geteuid(),
              info.st_nlink == 1, info.st_mode & 0o077 == 0 else { throw Failure.unsafeStore }
        guard flock(writer, LOCK_EX | LOCK_NB) == 0 else { throw Failure.busy }
        defer { flock(writer, LOCK_UN) }
        let root = ASRComponentResolver.defaultRoot(applicationSupport: applicationSupport)
        var existing = stat()
        if fstatat(parent, "Components", &existing, AT_SYMLINK_NOFOLLOW) == 0 {
            return try ModelStore(root: root).importModel(source: source, modelID: modelID, manifest: manifest)
        }
        guard errno == ENOENT else { throw Failure.unsafeStore }
        let name = ".model-import-" + UUID().uuidString
        guard mkdirat(parent, name, 0o700) == 0 else { throw Failure.unsafeStore }
        let temporary = applicationSupport.appendingPathComponent("VoxLog/" + name, isDirectory: true)
        var published = false
        defer { if !published { try? FileManager.default.removeItem(at: temporary) } }
        _ = try ModelStore(root: temporary).importModel(source: source, modelID: modelID, manifest: manifest)
        try Task.checkCancellation()
        guard renameatx_np(parent, name, parent, "Components", UInt32(RENAME_EXCL)) == 0 else {
            if errno == EEXIST || errno == ENOTEMPTY { throw Failure.busy }
            throw Failure.unsafeStore
        }
        published = true
        guard fsync(parent) == 0 else { throw Failure.installed }
        return try ASRComponentResolver.verifiedModel(in: root, modelID: modelID, manifest: manifest)
    }
}
