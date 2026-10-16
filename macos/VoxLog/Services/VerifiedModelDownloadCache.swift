import Foundation
import Darwin

/// Persistent complete-file checkpoints, never URLSession resumeData or a model activation receipt.
/// Lifetime lease serializes this mirror/catalog job; every reuse rehashes trusted catalog bytes.
final class VerifiedModelDownloadCache {
    enum Failure: Error { case unavailable, busy, capacity, absent }
    let root: URL
    private let lease: Int32
    private let rootFD: Int32
    private let containerFD: Int32
    private var retired = false
    private let name: String
    private let copyFile: (URL, URL) throws -> Void
    static func identifier(mirror: URL, manifest: ModelManifest) throws -> String {
        try PinnedModelDownload.validateMirror(mirror)
        return ModelManifest.hash(Data(("vox-model-checkpoint/v1|" + mirror.absoluteString + "|" + manifest.digest).utf8))
    }
    init(parent: URL, mirror: URL, manifest: ModelManifest, existingOnly: Bool = false, copyFile: @escaping (URL, URL) throws -> Void = { try FileManager.default.copyItem(at: $0, to: $1) }) throws {
        let identifier = try Self.identifier(mirror: mirror, manifest: manifest)
        let name = ".qwen-checkpoint-" + identifier
        let parentFD = try ModelTreeVerifier.openRoot(parent)
        defer { close(parentFD) }
        let containerName = "VoxAgentModelDownloadCheckpoints"
        if existingOnly {
            var existing = stat()
            if fstatat(parentFD, containerName, &existing, AT_SYMLINK_NOFOLLOW) != 0 {
                guard errno == ENOENT else { throw Failure.unavailable }
                throw Failure.absent
            }
        } else if mkdirat(parentFD, containerName, 0o700) != 0 && errno != EEXIST { throw Failure.unavailable }
        let containerFD = openat(parentFD, containerName, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard containerFD >= 0 else { throw Failure.unavailable }
        var containerOwned = false
        defer { if !containerOwned { close(containerFD) } }
        var containerInfo = stat()
        guard fstat(containerFD, &containerInfo) == 0, containerInfo.st_uid == geteuid(), containerInfo.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        let container = parent.appendingPathComponent(containerName, isDirectory: true)
        if existingOnly {
            var existing = stat()
            if fstatat(containerFD, name, &existing, AT_SYMLINK_NOFOLLOW) != 0 {
                guard errno == ENOENT else { throw Failure.unavailable }
                throw Failure.absent
            }
            guard existing.st_mode & S_IFMT == S_IFDIR else { throw Failure.unavailable }
        }
        let admission = openat(containerFD, ".admission.lock", O_RDWR | (existingOnly ? 0 : O_CREAT) | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
        guard admission >= 0 else { throw Failure.unavailable }; defer { close(admission) }
        var admissionInfo = stat()
        guard fstat(admission, &admissionInfo) == 0, admissionInfo.st_mode & S_IFMT == S_IFREG,
              admissionInfo.st_uid == geteuid(), admissionInfo.st_nlink == 1, admissionInfo.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        guard flock(admission, LOCK_EX | LOCK_NB) == 0 else { throw Failure.busy }
        // Hard bound on retained mirror/catalog jobs; accepted installs remove their own complete artifacts.
        // Stale or foreign files are preserved, never swept based only on a filename.
        let entries = existingOnly ? [] : try Self.boundedNames(containerFD)
        let jobs = entries.filter { $0.range(of: #"^\.qwen-checkpoint-[a-f0-9]{64}$"#, options: .regularExpression) != nil }
        if !existingOnly {
            if !jobs.contains(name) && jobs.count >= 4 { throw Failure.capacity }
            let newEntries = (entries.contains(name) ? 0 : 1) + (entries.contains(name + ".lock") ? 0 : 1)
            guard entries.count + newEntries <= 128 else { throw Failure.capacity }
        }
        let lockFD = openat(containerFD, name + ".lock", O_RDWR | (existingOnly ? 0 : O_CREAT) | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
        guard lockFD >= 0 else { throw Failure.unavailable }
        var successful = false
        defer { if !successful { close(lockFD) } }
        var info = stat()
        guard fstat(lockFD, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == geteuid(), info.st_nlink == 1, info.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        guard flock(lockFD, LOCK_EX | LOCK_NB) == 0 else { throw Failure.busy }
        if !existingOnly && mkdirat(containerFD, name, 0o700) != 0 && errno != EEXIST { throw Failure.unavailable }
        let fd = openat(containerFD, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else {
            if existingOnly && errno == ENOENT { throw Failure.absent }
            throw Failure.unavailable
        }
        guard fstat(fd, &info) == 0, info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else {
            close(fd); throw Failure.unavailable
        }
        self.root = container.appendingPathComponent(name, isDirectory: true)
        self.lease = lockFD; self.rootFD = fd; self.containerFD = containerFD; self.name = name; self.copyFile = copyFile; successful = true; containerOwned = true
    }
    deinit { close(rootFD); close(lease); close(containerFD) }
    private static func boundedNames(_ fd: Int32) throws -> [String] {
        // A fresh directory description avoids sharing the previous readdir offset.
        let scan = openat(fd, ".", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard scan >= 0 else { throw Failure.unavailable }
        guard let stream = fdopendir(scan) else { close(scan); throw Failure.unavailable }
        defer { closedir(stream) }
        var names: [String] = []
        while true {
            errno = 0
            guard let entry = readdir(stream) else {
                guard errno == 0 else { throw Failure.unavailable }; return names
            }
            let name = withUnsafePointer(to: &entry.pointee.d_name) {
                $0.withMemoryRebound(to: CChar.self, capacity: Int(MAXNAMLEN) + 1) { String(cString: $0) }
            }
            if name == "." || name == ".." { continue }
            guard names.count < 128 else { throw Failure.capacity }
            names.append(name)
        }
    }
    private func flat(_ entry: ModelManifest.Entry, name: String? = nil) -> ModelManifest.Entry {
        .init(path: name ?? entry.sha256, size: entry.size, sha256: entry.sha256)
    }
    func contains(_ entry: ModelManifest.Entry) throws -> Bool {
        guard !retired else { throw Failure.unavailable }
        var info = stat()
        if fstatat(rootFD, entry.sha256, &info, AT_SYMLINK_NOFOLLOW) != 0 {
            guard errno == ENOENT else { throw Failure.unavailable }; return false
        }
        guard info.st_mode & S_IFMT == S_IFREG, info.st_uid == geteuid(), info.st_nlink == 1,
              info.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        try ModelTreeVerifier.verify(entry: flat(entry), root: root)
        return true
    }
    /// Recover only verified metadata with invalid catalog bytes. Preserve the original bytes
    /// under a create-only quarantine name; unsafe metadata and read failures still refuse.
    func reusableOrQuarantine(_ entry: ModelManifest.Entry) throws -> Bool {
        do { return try contains(entry) }
        catch ModelTreeVerifier.Failure.hashMismatch { }
        catch ModelTreeVerifier.Failure.sizeMismatch { }
        let admission = openat(containerFD, ".admission.lock", O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard admission >= 0 else { throw Failure.unavailable }
        defer { close(admission) }
        var lockInfo = stat()
        guard fstat(admission, &lockInfo) == 0, lockInfo.st_mode & S_IFMT == S_IFREG,
              lockInfo.st_uid == geteuid(), lockInfo.st_nlink == 1,
              lockInfo.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        guard flock(admission, LOCK_EX | LOCK_NB) == 0 else { throw Failure.busy }
        let names = try Self.boundedNames(containerFD)
        guard names.count < 128, names.filter({ $0.hasPrefix(".quarantine-") }).count < 4 else { throw Failure.capacity }
        var info = stat()
        guard fstatat(rootFD, entry.sha256, &info, AT_SYMLINK_NOFOLLOW) == 0,
              info.st_mode & S_IFMT == S_IFREG, info.st_uid == geteuid(),
              info.st_nlink == 1, info.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        let quarantine = ".quarantine-" + UUID().uuidString
        guard renameatx_np(rootFD, entry.sha256, containerFD, quarantine, UInt32(RENAME_EXCL)) == 0,
              fsync(rootFD) == 0, fsync(containerFD) == 0 else { throw Failure.unavailable }
        return false
    }
    func emit(_ entry: ModelManifest.Entry, to writer: (Data) throws -> Void) throws {
        guard try contains(entry) else { throw Failure.unavailable }
        let fd = openat(rootFD, entry.sha256, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard fd >= 0 else { throw Failure.unavailable }; defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == geteuid(), info.st_nlink == 1, info.st_mode & 0o077 == 0,
              info.st_size == entry.size else { throw Failure.unavailable }
        var buffer = [UInt8](repeating: 0, count: 1024 * 1024), total: Int64 = 0
        while true {
            try Task.checkCancellation()
            let count = buffer.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, $0.count) }
            if count < 0 && errno == EINTR { continue }
            guard count >= 0 else { throw Failure.unavailable }
            if count == 0 { break }
            total += Int64(count); guard total <= entry.size else { throw Failure.unavailable }
            try writer(Data(buffer.prefix(count)))
        }
        guard total == entry.size else { throw Failure.unavailable }
        // The destination is verified again before installation; this read is not a lasting trust receipt.
    }
    func retain(_ entry: ModelManifest.Entry, from source: URL) throws {
        if try contains(entry) { return }
        try ModelTreeVerifier.verify(entry: entry, root: source)
        let name = ".partial-" + UUID().uuidString
        let temporary = root.appendingPathComponent(name)
        var ownsTemporary = false
        defer { if ownsTemporary { try? FileManager.default.removeItem(at: temporary) } }
        var existing = stat()
        guard fstatat(rootFD, name, &existing, AT_SYMLINK_NOFOLLOW) != 0, errno == ENOENT else { throw Failure.unavailable }
        ownsTemporary = true // Includes a partially created output when copy reports failure.
        try copyFile(source.appendingPathComponent(entry.path), temporary)
        // Copies can preserve readable source modes. Only the owned output is made private;
        // never chmod the caller's source or a linked/non-regular destination.
        let privateFD = openat(rootFD, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard privateFD >= 0 else { throw Failure.unavailable }
        var privateInfo = stat()
        let privatized = fstat(privateFD, &privateInfo) == 0
            && privateInfo.st_mode & S_IFMT == S_IFREG
            && privateInfo.st_uid == geteuid() && privateInfo.st_nlink == 1
            && fchmod(privateFD, 0o600) == 0
        close(privateFD)
        guard privatized else { throw Failure.unavailable }
        try ModelTreeVerifier.verify(entry: flat(entry, name: name), root: root)
        let fd = openat(rootFD, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { throw Failure.unavailable }
        let flushed = fsync(fd) == 0; close(fd)
        guard flushed, renameatx_np(rootFD, name, rootFD, entry.sha256, UInt32(RENAME_EXCL)) == 0 else { throw Failure.unavailable }
        ownsTemporary = false
        guard fsync(rootFD) == 0 else { throw Failure.unavailable }
    }
    /// Call only after importer success; removes only this job's reverified known model bytes.
    /// Unexpected files and crash leftovers prevent directory removal and remain for explicit operator review.
    func discardVerified(manifest: ModelManifest) throws {
        var hashes = Set<String>()
        for entry in manifest.files where hashes.insert(entry.sha256).inserted {
            do {
                if try contains(entry) {
                    guard unlinkat(rootFD, entry.sha256, 0) == 0 else { throw Failure.unavailable }
                }
            } catch ModelTreeVerifier.Failure.hashMismatch { }
              catch ModelTreeVerifier.Failure.sizeMismatch { }
        }
        guard fsync(rootFD) == 0 else { throw Failure.unavailable }
        var opened = stat(), named = stat()
        guard fstat(rootFD, &opened) == 0, fstatat(containerFD, name, &named, AT_SYMLINK_NOFOLLOW) == 0,
              opened.st_dev == named.st_dev, opened.st_ino == named.st_ino else { throw Failure.unavailable }
        // rmdir cannot recurse through or delete unexpected contents. Preserve the entire
        // remaining job after accepted installation so crash files do not block this slot.
        if unlinkat(containerFD, name, AT_REMOVEDIR) != 0 {
            guard errno == ENOTEMPTY || errno == EEXIST else { throw Failure.unavailable }
            let admission = openat(containerFD, ".admission.lock", O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
            guard admission >= 0 else { throw Failure.unavailable }
            defer { close(admission) }
            var lockInfo = stat()
            guard fstat(admission, &lockInfo) == 0, lockInfo.st_mode & S_IFMT == S_IFREG,
                  lockInfo.st_uid == geteuid(), lockInfo.st_nlink == 1,
                  lockInfo.st_mode & 0o077 == 0 else { throw Failure.unavailable }
            guard flock(admission, LOCK_EX | LOCK_NB) == 0 else { throw Failure.busy }
            let names = try Self.boundedNames(containerFD)
            guard names.count < 128, names.filter({ $0.hasPrefix(".retained-job-") }).count < 4 else { throw Failure.capacity }
            guard fstatat(containerFD, name, &named, AT_SYMLINK_NOFOLLOW) == 0,
                  opened.st_dev == named.st_dev, opened.st_ino == named.st_ino else { throw Failure.unavailable }
            guard renameatx_np(containerFD, name, containerFD, ".retained-job-" + UUID().uuidString,
                              UInt32(RENAME_EXCL)) == 0 else { throw Failure.unavailable }
        }
        retired = true
        guard fsync(containerFD) == 0 else { throw Failure.unavailable }
    }

}
