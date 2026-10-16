import CryptoKit
import Darwin
import Foundation

/// Read-only validation of listed file bytes (extra files are not enumerated).
/// Importers must copy only listed files into an owned staging tree. This is not a persistent trust receipt
/// Root must be canonical and symlink-free (including ancestors).
/// verify requires caller-owned staging/cache files with no hard links; stage copies untrusted source bytes.
/// It is not a lease: callers must own the staging tree until atomic store publication.
enum ModelTreeVerifier {
    enum Failure: Error, Equatable {
        case unsafeRoot, unsafeFile(String), sizeMismatch(String), hashMismatch(String), readFailed
        case stagingFailed, writeFailed, insufficientSpace
    }

    /// Creates a private temporary copy. Caller owns cleanup; no cache publication or activation.
    /// Only trusted manifest entries are copied, never extra files or symbolic links.
    /// Sources may be foreign-owned or hard-linked; opened bytes must match the trusted manifest.
    /// Sources cannot be group/world-writable. The private copy must be owned and single-linked.
    static func stage(root: URL, manifest: ModelManifest, stagingParent parent: URL) throws -> URL {
        // Caller supplies a private canonical parent on the intended store volume.
        let parentFD = try openRoot(parent)
        defer { close(parentFD) }
        var volume = statfs()
        guard fstatfs(parentFD, &volume) == 0, volume.f_bsize > 0 else { throw Failure.stagingFailed }
        let required = UInt64(manifest.totalBytes) + 64 * 1024 * 1024
        guard volume.f_bavail >= (required + UInt64(volume.f_bsize) - 1) / UInt64(volume.f_bsize) else {
            throw Failure.insufficientSpace
        }
        var template = Array(parent.appendingPathComponent("vox-model-stage-XXXXXX").path.utf8CString)
        let created: String? = template.withUnsafeMutableBufferPointer { buffer in
            guard let path = mkdtemp(buffer.baseAddress!) else { return nil }
            return String(cString: path)
        }
        guard let created else { throw Failure.stagingFailed }
        let destination = URL(fileURLWithPath: created, isDirectory: true)
        var succeeded = false
        defer { if !succeeded { try? FileManager.default.removeItem(at: destination) } }
        try inspect(root: root, manifest: manifest, copyingTo: destination)
        try verify(root: destination, manifest: manifest)
        succeeded = true
        return destination
    }

    static func verify(root: URL, manifest: ModelManifest) throws {
        try inspect(root: root, manifest: manifest, copyingTo: nil)
    }

    private static func inspect(root: URL, manifest: ModelManifest, copyingTo destination: URL?) throws {
        let rootFD = try openDirectory(root, requireOwned: destination == nil)
        defer { close(rootFD) }
        for entry in manifest.files {
            try Task.checkCancellation()
            try verify(entry: entry, rootFD: rootFD, copyingTo: destination)
        }
    }

    static func verify(entry: ModelManifest.Entry, root: URL) throws {
        let descriptor = try openRoot(root)
        defer { close(descriptor) }
        try verify(entry: entry, rootFD: descriptor, copyingTo: nil)
    }

    static func openRoot(_ root: URL) throws -> Int32 {
        try openDirectory(root, requireOwned: true)
    }

    private static func openDirectory(_ root: URL, requireOwned: Bool) throws -> Int32 {
        guard root.isFileURL, root.path.hasPrefix("/"), !root.path.utf8.contains(0) else { throw Failure.unsafeRoot }
        // Walk from / using descriptors so symlink ancestors cannot redirect inspection.
        var rootFD = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        guard rootFD >= 0 else { throw Failure.unsafeRoot }
        var success = false
        defer { if !success { close(rootFD) } }
        for part in root.path.split(separator: "/") {
            guard part != ".", part != ".." else { throw Failure.unsafeRoot }
            let next = openat(rootFD, String(part), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard next >= 0 else { throw Failure.unsafeRoot }
            close(rootFD)
            rootFD = next
        }
        var rootMetadata = stat()
        guard fstat(rootFD, &rootMetadata) == 0,
              (!requireOwned || rootMetadata.st_uid == geteuid()), rootMetadata.st_mode & 0o022 == 0 else {
            throw Failure.unsafeRoot
        }
        success = true
        return rootFD
    }

    private static func verify(entry: ModelManifest.Entry, rootFD: Int32, copyingTo destination: URL?) throws {
        var parent = dup(rootFD)
        guard parent >= 0 else { throw Failure.readFailed }
        defer { close(parent) }
        let parts = entry.path.split(separator: "/").map(String.init)
        for part in parts.dropLast() {
            let next = openat(parent, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard next >= 0 else { throw Failure.unsafeFile(entry.path) }
            close(parent)
            parent = next
            var directory = stat()
            guard fstat(parent, &directory) == 0,
                  (destination != nil || directory.st_uid == geteuid()), directory.st_mode & 0o022 == 0 else {
                throw Failure.unsafeFile(entry.path)
            }
        }
        // Reject obvious special files before opening; fstat below still checks the actual opened inode.
        var listed = stat()
        guard fstatat(parent, parts.last!, &listed, AT_SYMLINK_NOFOLLOW) == 0,
              listed.st_mode & S_IFMT == S_IFREG else { throw Failure.unsafeFile(entry.path) }
        let fd = openat(parent, parts.last!, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY | O_CLOEXEC)
        guard fd >= 0 else { throw Failure.unsafeFile(entry.path) }
        defer { close(fd) }
        var metadata = stat()
        guard fstat(fd, &metadata) == 0, metadata.st_mode & S_IFMT == S_IFREG,
              (destination != nil || (metadata.st_nlink == 1 && metadata.st_uid == geteuid())),
              metadata.st_mode & 0o022 == 0 else {
            throw Failure.unsafeFile(entry.path)
        }
        guard metadata.st_size == entry.size else { throw Failure.sizeMismatch(entry.path) }
        var output: Int32 = -1
        defer { if output >= 0 { close(output) } }
        if let destination {
            let file = destination.appendingPathComponent(entry.path)
            do {
                try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                        attributes: [.posixPermissions: 0o700])
            } catch { throw Failure.stagingFailed }
            output = open(file.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
            guard output >= 0 else { throw Failure.writeFailed }
        }
        var hasher = SHA256()
        var bytes: Int64 = 0
        var buffer = [UInt8](repeating: 0, count: 1024 * 1024)
        while true {
            try Task.checkCancellation()
            let count = buffer.withUnsafeMutableBytes { raw in
                Darwin.read(fd, raw.baseAddress, raw.count)
            }
            if count < 0 {
                if errno == EINTR { continue }
                throw Failure.readFailed
            }
            if count == 0 { break }
            bytes += Int64(count)
            guard bytes <= entry.size else { throw Failure.sizeMismatch(entry.path) }
            if output >= 0 {
                try buffer.withUnsafeBytes { raw in
                    var offset = 0
                    while offset < count {
                        let written = Darwin.write(output, raw.baseAddress!.advanced(by: offset), count - offset)
                        if written < 0 && errno == EINTR { continue }
                        guard written > 0 else { throw Failure.writeFailed }
                        offset += written
                    }
                }
            }
            hasher.update(data: Data(buffer.prefix(count)))
        }
        guard bytes == entry.size else { throw Failure.sizeMismatch(entry.path) }
        let digest = hasher.finalize().map { String(format: "%02x", $0) }.joined()
        guard digest == entry.sha256 else { throw Failure.hashMismatch(entry.path) }
        if output >= 0 {
            guard fsync(output) == 0 else { throw Failure.writeFailed }
        }
    }
}
