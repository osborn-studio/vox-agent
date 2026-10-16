import Foundation
import CryptoKit
import Darwin

struct ContextPrivateSavedDraft: Codable, Equatable {
    struct Reference: Codable, Equatable { let id: String; let revision: Int }
    let version: Int
    let scope: String
    let text: String
    let references: [Reference]
    let savedAt: String
    var valid: Bool {
        version == 1 && scope.utf8.count == 64 && scope.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
        && text.unicodeScalars.count <= 300000 && !text.contains("\0")
        && (1...100).contains(references.count) && Set(references.map(\.id)).count == references.count
        && references.allSatisfy { ContextWeeklySource.identifier($0.id, maximum: 128) && (1...1_000_000).contains($0.revision) }
        && savedAt.count <= 80 && ContextWeeklySource.timestamp(savedAt) != nil
    }
}

/// One encrypted latest draft per opaque identity/origin scope, outside exported chat directories.
/// Explicit operations only; no initialization I/O or automatic upload.
final class ContextPrivateDraftStore {
    enum Failure: Error { case unavailable, invalid, installed, capacity }
    private let directory: URL
    private let durabilityBoundary: URL
    private let syncParent: (Int32) -> Bool
    private let readKey: () throws -> String?
    private let saveKey: (String) throws -> Void
    private let replace: (String, String) -> Bool
    private let lock = NSLock()
    static let keyName = "vox_private_context_drafts_aes_v1"
    init(directory: URL? = nil, durabilityBoundary: URL? = nil,
         readKey: @escaping () throws -> String? = { try KeychainService.loadSecureScalarStrict(key: keyName) },
         saveKey: @escaping (String) throws -> Void = { try KeychainService.addSecureScalarIfAbsent(key: keyName, value: $0) },
         replace: @escaping (String, String) -> Bool = { rename($0, $1) == 0 },
         syncParent: @escaping (Int32) -> Bool = { fsync($0) == 0 && fcntl($0, F_FULLFSYNC) == 0 }) {
        self.directory = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("VoxAgentPrivateContext/Drafts", isDirectory: true)
        self.durabilityBoundary = durabilityBoundary ?? (directory?.deletingLastPathComponent().deletingLastPathComponent()
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].deletingLastPathComponent())
        self.syncParent = syncParent
        self.readKey = readKey; self.saveKey = saveKey; self.replace = replace
    }
    private func validateDirectoryChain(allowMissing: Bool) throws {
        let basePath = durabilityBoundary.standardizedFileURL.path
        let targetPath = directory.standardizedFileURL.path
        guard targetPath.hasPrefix(basePath + "/") else { throw Failure.unavailable }
        var ancestor = directory.standardizedFileURL, depth = 0
        while true {
            var checked = stat()
            if lstat(ancestor.path, &checked) == 0 {
                guard (checked.st_mode & S_IFMT) == S_IFDIR, checked.st_uid == geteuid(),
                      ancestor.path == basePath || checked.st_mode & 0o022 == 0 else { throw Failure.unavailable }
            } else {
                guard errno == ENOENT, allowMissing, ancestor.path != basePath else { throw Failure.unavailable }
            }
            if ancestor.path == basePath { break }
            ancestor = ancestor.deletingLastPathComponent(); depth += 1
            guard depth <= 32 else { throw Failure.unavailable }
        }
    }
    private func checkDirectory(create: Bool) throws {
        try validateDirectoryChain(allowMissing: create)
        if create {
            var boundary = stat()
            let base = durabilityBoundary.standardizedFileURL.path, path = directory.standardizedFileURL.path
            guard path.hasPrefix(base + "/"), lstat(base, &boundary) == 0,
                  (boundary.st_mode & S_IFMT) == S_IFDIR, boundary.st_uid == geteuid() else { throw Failure.unavailable }
        }
        if !FileManager.default.fileExists(atPath: directory.path) {
            guard create else { throw Failure.unavailable }
            var missing: [URL] = [], item = directory
            while !FileManager.default.fileExists(atPath: item.path) {
                missing.append(item); item = item.deletingLastPathComponent()
                guard missing.count <= 32 else { throw Failure.unavailable }
            }
            for entry in missing.reversed() {
                try FileManager.default.createDirectory(at: entry, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
                try flushDirectory(entry)
                try flushDirectory(entry.deletingLastPathComponent())
            }
        }
        if create {
            // Reflush the configured bootstrap chain even if an earlier interrupted attempt left directories visible.
            var entry = directory, steps = 0
            while true {
                try flushDirectory(entry)
                if entry.standardizedFileURL.path == durabilityBoundary.standardizedFileURL.path { break }
                entry = entry.deletingLastPathComponent(); steps += 1
                guard steps <= 32, entry.path != "/" else { throw Failure.unavailable }
            }
        }
        var info = stat()
        guard lstat(directory.path, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
              info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else { throw Failure.unavailable }
    }
    private func flushDirectory(_ directory: URL) throws {
        let parent = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard parent >= 0 else { throw Failure.unavailable }
        defer { close(parent) }
        guard syncParent(parent) else { throw Failure.unavailable }
    }
    private func processLock() throws -> Int32 {
        let descriptor = open(directory.appendingPathComponent(".vault.lock").path, O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { throw Failure.unavailable }
        var info = stat()
        guard fstat(descriptor, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_uid == geteuid(), info.st_nlink == 1, info.st_mode & 0o077 == 0,
              flock(descriptor, LOCK_EX | LOCK_NB) == 0 else { close(descriptor); throw Failure.unavailable }
        return descriptor
    }
    private func boundedEntries() throws -> [URL] {
        // Stream at most 256 direct entries, rather than allocating an unbounded directory listing.
        let fd = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { throw Failure.unavailable }
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
              info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else { close(fd); throw Failure.unavailable }
        guard let stream = fdopendir(fd) else { close(fd); throw Failure.unavailable }
        defer { closedir(stream) }
        var result: [URL] = []
        while true {
            errno = 0
            guard let entry = readdir(stream) else {
                guard errno == 0 else { throw Failure.unavailable }
                return result
            }
            let name = withUnsafePointer(to: &entry.pointee.d_name) {
                $0.withMemoryRebound(to: CChar.self, capacity: Int(MAXNAMLEN) + 1) { String(cString: $0) }
            }
            if name == "." || name == ".." { continue }
            guard result.count < 256 else { throw Failure.capacity }
            result.append(directory.appendingPathComponent(name))
        }
    }
    private func key(create: Bool) throws -> SymmetricKey {
        if let encoded = try readKey() {
            guard let bytes = Data(base64Encoded: encoded), bytes.count == 32 else { throw Failure.unavailable }
            return SymmetricKey(data: bytes)
        }
        // Never replace a missing key while any encrypted draft may depend on it.
        guard create, try boundedEntries().filter({ $0.lastPathComponent != ".vault.lock" }).isEmpty else { throw Failure.unavailable }
        let key = SymmetricKey(size: .bits256)
        let bytes = key.withUnsafeBytes { Data($0) }
        try saveKey(bytes.base64EncodedString())
        guard try readKey() == bytes.base64EncodedString() else { throw Failure.unavailable }
        return key
    }
    private func file(_ scope: String) throws -> URL {
        guard scope.utf8.count == 64, scope.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else { throw Failure.invalid }
        return directory.appendingPathComponent(scope + ".v1.aes")
    }
    private func aad(_ scope: String) -> Data { Data(("vox-private-draft/v1|" + scope).utf8) }
    func save(_ draft: ContextPrivateSavedDraft) throws {
        lock.lock(); defer { lock.unlock() }
        guard draft.valid else { throw Failure.invalid }
        let target = try file(draft.scope)
        try checkDirectory(create: true)
        let lease = try processLock(); defer { close(lease) }
        var prior = stat()
        if lstat(target.path, &prior) == 0 {
            guard (prior.st_mode & S_IFMT) == S_IFREG, prior.st_uid == geteuid(), prior.st_nlink == 1,
                  prior.st_mode & 0o077 == 0 else { throw Failure.unavailable }
            // A restored or replaced Keychain key must not destroy the only older ciphertext.
            let previous = try read(scope: draft.scope)
            if previous == draft {
                do { try flushDirectory(directory) } catch { throw Failure.installed }
                return
            }
        } else if errno != ENOENT { throw Failure.unavailable }
        let plain = try JSONEncoder().encode(draft)
        guard plain.count + 28 <= 2_000_000 else { throw Failure.invalid }
        let entries = try boundedEntries()
        let snapshots = entries.filter { $0.lastPathComponent.range(of: #"^[a-f0-9]{64}\.v1\.aes\z"#, options: .regularExpression) != nil }
        guard snapshots.contains(where: { $0.lastPathComponent == target.lastPathComponent }) || (snapshots.count < 64 && entries.count < 256) else { throw Failure.capacity }
        var retainedBytes = 0
        for item in snapshots where item.lastPathComponent != target.lastPathComponent { retainedBytes += try fileData(item).count }
        guard retainedBytes + plain.count + 28 <= 64_000_000 else { throw Failure.capacity }
        let bytes = try AES.GCM.seal(plain, using: key(create: true), authenticating: aad(draft.scope)).combined!
        guard bytes.count <= 2_000_000 else { throw Failure.invalid }
        var template = Array(directory.appendingPathComponent(".draft-XXXXXX").path.utf8CString)
        let descriptor = mkstemp(&template)
        guard descriptor >= 0 else { throw Failure.unavailable }
        let temporary = String(cString: template)
        var installed = false
        defer { close(descriptor); if !installed { unlink(temporary) } }
        guard fchmod(descriptor, 0o600) == 0, fcntl(descriptor, F_SETFD, FD_CLOEXEC) == 0 else { throw Failure.unavailable }
        try bytes.withUnsafeBytes { pointer in
            var position = 0
            while position < bytes.count {
                let count = Darwin.write(descriptor, pointer.baseAddress!.advanced(by: position), bytes.count - position)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw Failure.unavailable }
                position += count
            }
        }
        guard fsync(descriptor) == 0, fcntl(descriptor, F_FULLFSYNC) == 0, replace(temporary, target.path) else { throw Failure.unavailable }
        installed = true
        let parent = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard parent >= 0 else { throw Failure.installed }
        defer { close(parent) }
        guard syncParent(parent) else { throw Failure.installed }
    }
    func load(scope: String) throws -> ContextPrivateSavedDraft {
        lock.lock(); defer { lock.unlock() }
        _ = try file(scope)
        try checkDirectory(create: false)
        let lease = try processLock(); defer { close(lease) }
        return try read(scope: scope)
    }
    private func read(scope: String) throws -> ContextPrivateSavedDraft {
        let path = try file(scope)
        let data = try fileData(path)
        let plain = try AES.GCM.open(AES.GCM.SealedBox(combined: data), using: key(create: false), authenticating: aad(scope))
        guard plain.count <= 2_000_000,
              let object = try JSONSerialization.jsonObject(with: plain) as? [String: Any],
              Set(object.keys) == Set(["version", "scope", "text", "references", "savedAt"]),
              let refs = object["references"] as? [[String: Any]], (1...100).contains(refs.count), refs.allSatisfy({ Set($0.keys) == Set(["id", "revision"]) }) else { throw Failure.invalid }
        let draft = try JSONDecoder().decode(ContextPrivateSavedDraft.self, from: plain)
        guard draft.valid, draft.scope == scope else { throw Failure.invalid }
        return draft
    }
    private func fileData(_ path: URL) throws -> Data {
        let descriptor = open(path.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard descriptor >= 0 else { throw Failure.unavailable }
        defer { close(descriptor) }
        var info = stat()
        guard fstat(descriptor, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_uid == geteuid(), info.st_nlink == 1, info.st_mode & 0o077 == 0,
              info.st_size > 0, info.st_size <= 2_000_000 else { throw Failure.unavailable }
        var data = Data(count: Int(info.st_size))
        let dataSize = data.count
        try data.withUnsafeMutableBytes { pointer in
            var position = 0
            while position < dataSize {
                let count = Darwin.read(descriptor, pointer.baseAddress!.advanced(by: position), dataSize - position)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw Failure.unavailable }
                position += count
            }
        }
        return data
    }

}
