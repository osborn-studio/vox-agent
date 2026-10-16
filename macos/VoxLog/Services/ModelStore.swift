import Darwin
import Foundation

/// Append-only, offline store. Supply an App-trusted manifest and a canonical owned root.
/// No deletion/replacement API: URLs remain stable for cooperating readers. ASR leases are separate.
struct ModelStore {
    enum Failure: Error { case unsafeStore, busy, invalidModelID, publicationFailed, insufficientSpace }
    let root: URL

    func importModel(source: URL, modelID: String, manifest: ModelManifest) throws -> URL {
        guard Self.validModelID(modelID) else { throw Failure.invalidModelID }
        let rootFD = try ModelTreeVerifier.openRoot(root)
        defer { close(rootFD) }
        let lock = openat(rootFD, ".writer.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
        guard lock >= 0 else { throw Failure.unsafeStore }
        defer { close(lock) }
        var info = stat()
        guard fstat(lock, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_nlink == 1, info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else { throw Failure.unsafeStore }
        guard flock(lock, LOCK_EX | LOCK_NB) == 0 else { throw Failure.busy }
        defer { flock(lock, LOCK_UN) }

        let blobs = try directory(root.appendingPathComponent("blobs"))
        let sha = try directory(blobs.appendingPathComponent("sha256"))
        let models = try directory(root.appendingPathComponent("models"))
        let family = try directory(models.appendingPathComponent(modelID))
        let receipts = try directory(root.appendingPathComponent("receipts"))
        let staging = try directory(root.appendingPathComponent("staging"))
        let target = family.appendingPathComponent(manifest.digest, isDirectory: true)
        if exists(target) {
            try ModelTreeVerifier.verify(root: target, manifest: manifest)
            try receipt(manifest: manifest, directory: receipts, staging: staging)
            return target // Source may have disappeared; valid cache requires no source or network.
        }

        // Verify cached content before allowing it to reduce the capacity estimate.
        var cachedHashes = Set<String>()
        for entry in manifest.files {
            if exists(sha.appendingPathComponent(entry.sha256)) {
                try ModelTreeVerifier.verify(root: sha, manifest: blobManifest(entry))
                cachedHashes.insert(entry.sha256)
            }
        }
        var volume = statfs()
        guard fstatfs(rootFD, &volume) == 0, volume.f_bsize > 0 else { throw Failure.unsafeStore }
        let required = Self.requiredImportBytes(manifest: manifest, cachedHashes: cachedHashes)
        guard volume.f_bavail >= (required + UInt64(volume.f_bsize) - 1) / UInt64(volume.f_bsize) else {
            throw Failure.insufficientSpace
        }
        var sourceStage: URL?
        defer { if let sourceStage { try? FileManager.default.removeItem(at: sourceStage) } }
        for entry in manifest.files {
            let blob = sha.appendingPathComponent(entry.sha256)
            if cachedHashes.contains(entry.sha256) { continue }
            if sourceStage == nil {
                sourceStage = try ModelTreeVerifier.stage(root: source, manifest: manifest, stagingParent: staging)
            }
            let copied = sourceStage!.appendingPathComponent(entry.path)
            guard chmod(copied.path, 0o444) == 0 else { throw Failure.publicationFailed }
            try syncFile(copied)
            try publish(copied, to: blob)
            cachedHashes.insert(entry.sha256)
        }

        let projection = try temporaryDirectory(in: staging)
        defer { try? FileManager.default.removeItem(at: projection) }
        for entry in manifest.files {
            let file = projection.appendingPathComponent(entry.path)
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            // Copies deliberately avoid shared writable inodes and preserve the verifier's nlink=1 rule.
            try FileManager.default.copyItem(at: sha.appendingPathComponent(entry.sha256), to: file)
        }
        try ModelTreeVerifier.verify(root: projection, manifest: manifest)
        var directories = Set<URL>([projection])
        for entry in manifest.files {
            let file = projection.appendingPathComponent(entry.path)
            try syncFile(file)
            var parent = file.deletingLastPathComponent()
            while parent.path != projection.path {
                directories.insert(parent)
                parent.deleteLastPathComponent()
            }
        }
        for directory in directories.sorted(by: { $0.path.count > $1.path.count }) { try syncDirectory(directory) }
        try publish(projection, to: target)
        try receipt(manifest: manifest, directory: receipts, staging: staging)
        return target
    }

    static func validModelID(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 80 && !value.hasPrefix(".") &&
        !value.hasSuffix(".") && !value.contains("..") &&
        value.utf8.allSatisfy { (97...122).contains($0) || (48...57).contains($0) || [45, 46].contains($0) }
    }

    /// Upper bound for this copy-based transaction, not a filesystem reservation.
    /// Caller may count hashes as cached only after validating their size and bytes.
    static func requiredImportBytes(manifest: ModelManifest, cachedHashes: Set<String>) -> UInt64 {
        var missing = Set<String>()
        var missingBytes: UInt64 = 0
        for entry in manifest.files where !cachedHashes.contains(entry.sha256) {
            if missing.insert(entry.sha256).inserted { missingBytes += UInt64(entry.size) }
        }
        let sourceStageBytes = missing.isEmpty ? 0 : UInt64(manifest.totalBytes)
        return UInt64(manifest.totalBytes) + sourceStageBytes + missingBytes + 64 * 1024 * 1024
    }

    private func directory(_ url: URL) throws -> URL {
        if mkdir(url.path, 0o700) != 0 && errno != EEXIST { throw Failure.unsafeStore }
        let fd = try ModelTreeVerifier.openRoot(url)
        close(fd)
        try syncDirectory(url.deletingLastPathComponent())
        return url
    }

    private func exists(_ url: URL) -> Bool {
        var info = stat()
        return lstat(url.path, &info) == 0
    }

    private func temporaryDirectory(in parent: URL) throws -> URL {
        var name = Array(parent.appendingPathComponent("store-XXXXXX").path.utf8CString)
        let path = name.withUnsafeMutableBufferPointer { buffer -> String? in
            guard let result = mkdtemp(buffer.baseAddress!) else { return nil }
            return String(cString: result)
        }
        guard let path else { throw Failure.publicationFailed }
        return URL(fileURLWithPath: path)
    }

    private func publish(_ source: URL, to destination: URL) throws {
        // Same-volume exclusive rename: never replace an existing blob, model or receipt.
        guard renamex_np(source.path, destination.path, UInt32(RENAME_EXCL)) == 0 else { throw Failure.publicationFailed }
        try syncDirectory(destination.deletingLastPathComponent())
    }

    private func syncFile(_ url: URL) throws {
        let fd = open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { throw Failure.publicationFailed }
        defer { close(fd) }
        guard fsync(fd) == 0 else { throw Failure.publicationFailed }
    }

    private func syncDirectory(_ url: URL) throws {
        let fd = try ModelTreeVerifier.openRoot(url)
        defer { close(fd) }
        guard fsync(fd) == 0 else { throw Failure.publicationFailed }
    }

    private func blobManifest(_ entry: ModelManifest.Entry) throws -> ModelManifest {
        let canonical = "[{\"path\":\"\(entry.sha256)\",\"sha256\":\"\(entry.sha256)\",\"size\":\(entry.size)}]"
        return try ModelManifest(files: [.init(path: entry.sha256, size: entry.size, sha256: entry.sha256)],
                                 expectedDigest: ModelManifest.hash(Data(canonical.utf8)))
    }

    private func receipt(manifest: ModelManifest, directory: URL, staging: URL) throws {
        let target = directory.appendingPathComponent(manifest.digest + ".json")
        // Receipt is only a hint: the model above is always fully verified, even when this exists.
        if exists(target) { return }
        let temporary = try temporaryDirectory(in: staging)
        defer { try? FileManager.default.removeItem(at: temporary) }
        let file = temporary.appendingPathComponent("receipt")
        try Data("{\"schema\":1,\"manifest\":\"\(manifest.digest)\"}\n".utf8).write(to: file, options: .withoutOverwriting)
        guard chmod(file.path, 0o444) == 0 else { throw Failure.publicationFailed }
        try syncFile(file)
        try publish(file, to: target)
    }
}
