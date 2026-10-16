import Foundation
import CryptoKit
import Darwin

struct ContextPrivateSavedRequest: Codable {
    let version: Int
    let scope: String
    let pending: ContextPrivateInbox.PendingWrite
    let confirmed: Bool
    var valid: Bool {
        guard version == 1, scope.utf8.count == 64,
              scope.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              pending.data.count <= 128_000,
              pending.inputEditorId.map({ ContextWeeklySource.identifier($0, maximum: 128) }) ?? true,
              ContextWeeklySource.identifier(pending.eventId, maximum: 128),
              let body = try? JSONSerialization.jsonObject(with: pending.data) as? [String: Any] else { return false }
        guard (pending.projectId == nil) == (pending.repositoryId == nil) else { return false }
        if let project = pending.projectId, let repository = pending.repositoryId {
            guard ContextWeeklySource.identifier(project, maximum: 80), repository.utf8.count <= 200,
                  repository.range(of: #"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\z"#, options: .regularExpression) != nil,
                  !repository.contains(where: { $0.isWhitespace }),
                  !repository.components(separatedBy: "/").contains(where: { $0 == "." || $0 == ".." }) else { return false }
        }
        var expected: [String: Any] = [:]
        if let selected = pending.selected {
            guard ContextWeeklySource.identifier(selected.id, maximum: 128), selected.eventId == pending.eventId,
                  (1...999_999).contains(selected.revision), selected.createdAt.count <= 80, selected.changedAt.count <= 80,
                  let created = ContextWeeklySource.timestamp(selected.createdAt),
                  let changed = ContextWeeklySource.timestamp(selected.changedAt), changed >= created,
                  pending.path == "v1/context-private/" + selected.id + (pending.withdrawn ? "/withdraw" : ""),
                  pending.method == (pending.withdrawn ? "POST" : "PUT") else { return false }
            guard let nonce = pending.mutationId, ContextWeeklySource.identifier(nonce, maximum: 128) else { return false }
            expected["requestId"] = nonce; expected["expectedRevision"] = selected.revision
        } else {
            guard !pending.withdrawn, pending.mutationId == nil, pending.path == "v1/context-private", pending.method == "POST" else { return false }
            expected["eventId"] = pending.eventId; expected["visibility"] = "private"
            if let project = pending.projectId, let repository = pending.repositoryId { expected["projectId"] = project; expected["expectedRepositoryId"] = repository }
        }
        if pending.withdrawn {
            guard pending.text == nil, pending.tags == nil else { return false }
        } else {
            guard let text = pending.text, ContextPrivateSource.validText(text), let tags = pending.tags,
                  tags.count <= 5, Set(tags).count == tags.count, tags.allSatisfy(ContextPrivateSource.allowedTags.contains) else { return false }
            expected["text"] = text; expected["tags"] = tags
        }
        return (try? JSONSerialization.data(withJSONObject: body, options: .sortedKeys))
            == (try? JSONSerialization.data(withJSONObject: expected, options: .sortedKeys))
    }
}

/// One encrypted immutable unresolved request per opaque identity/origin scope, outside exported chat directories.
/// Explicit operations only; no initialization I/O or automatic upload.
final class ContextPrivateRequestStore {
    struct ArchiveReview: Equatable { let scope: String; let cipherFingerprint: String; let eventFingerprint: String }
    struct ArchiveFileInfo: Equatable, Identifiable {
        let id: String // Filename label only, never a validated content hash or delivery receipt.
        let bytes: Int
    }
    private struct Decision: Codable {
        let version: Int; let scope: String; let cipherFingerprint: String; let eventFingerprint: String; let state: String
    }
    enum Failure: Error { case unavailable, invalid, installed, capacity }
    private let directory: URL
    private let durabilityBoundary: URL
    private let readKey: () throws -> String?
    private let saveKey: (String) throws -> Void
    private let syncParent: (Int32) -> Bool
    private let replace: (String, String) -> Bool
    private let lock = NSLock()
    static let keyName = "vox_private_context_requests_aes_v1"
    init(directory: URL? = nil, durabilityBoundary: URL? = nil,
         readKey: @escaping () throws -> String? = { try KeychainService.loadSecureScalarStrict(key: keyName) },
         saveKey: @escaping (String) throws -> Void = { try KeychainService.addSecureScalarIfAbsent(key: keyName, value: $0) },
         replace: @escaping (String, String) -> Bool = { rename($0, $1) == 0 },
         syncParent: @escaping (Int32) -> Bool = { fsync($0) == 0 && fcntl($0, F_FULLFSYNC) == 0 }) {
        self.directory = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("VoxAgentPrivateContext/Requests", isDirectory: true)
        // Production boundary is the existing Library container. Injected roots may name their provisioned boundary.
        self.durabilityBoundary = durabilityBoundary ?? (directory?.deletingLastPathComponent().deletingLastPathComponent()
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].deletingLastPathComponent())
        self.readKey = readKey; self.saveKey = saveKey; self.replace = replace; self.syncParent = syncParent
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
    private func boundedEntries(in directory: URL) throws -> [URL] {
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
        // Never replace a missing key while any encrypted request may depend on it.
        guard create, try boundedEntries(in: directory).filter({ $0.lastPathComponent != ".vault.lock" }).isEmpty else { throw Failure.unavailable }
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
    private func aad(_ scope: String) -> Data { Data(("vox-private-request/v1|" + scope).utf8) }
    func save(_ draft: ContextPrivateSavedRequest) throws {
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
            let prior = try read(scope: draft.scope)
            if !prior.confirmed && !draft.confirmed && prior.pending == draft.pending {
                do { try flushDirectory(directory) } catch { throw Failure.installed }
                return // Idempotent unknown save preserves ciphertext and archive decision binding.
            }
            if !prior.confirmed && prior.pending != draft.pending {
                guard try archived(scope: draft.scope, bytes: fileData(target)) else { throw Failure.unavailable }
            }
        } else if errno != ENOENT { throw Failure.unavailable }
        let bytes = try AES.GCM.seal(JSONEncoder().encode(draft), using: key(create: true), authenticating: aad(draft.scope)).combined!
        guard bytes.count <= 2_000_000 else { throw Failure.invalid }
        try install(bytes, target: target)
    }
    private func checkRootAllocation(_ bytes: Data, target: URL) throws {
        let entries = try boundedEntries(in: directory)
        let replacing = entries.contains { $0.lastPathComponent == target.lastPathComponent }
        guard replacing || entries.count < 256 else { throw Failure.capacity }
        let snapshots = entries.filter { $0.lastPathComponent.range(of: #"^[a-f0-9]{64}\.v1\.aes\z"#, options: .regularExpression) != nil }
        if target.lastPathComponent.hasSuffix(".v1.aes") {
            guard replacing || snapshots.count < 64 else { throw Failure.capacity }
        }
        // Include recognized encrypted decision files in the root allocation budget; never decrypt another scope.
        let ciphers = entries.filter { $0.lastPathComponent.range(of: #"^[a-f0-9]{64}\.v1\.(aes|decision)\z"#, options: .regularExpression) != nil }
        var retainedBytes = 0
        for file in ciphers where file.lastPathComponent != target.lastPathComponent {
            retainedBytes += try fileSize(file)
            guard retainedBytes + bytes.count <= 64_000_000 else { throw Failure.capacity }
        }
        guard retainedBytes + bytes.count <= 64_000_000 else { throw Failure.capacity }
    }
    private func install(_ bytes: Data, target: URL, archive: Bool = false) throws {
        var old = stat()
        if lstat(target.path, &old) == 0 {
            guard (old.st_mode & S_IFMT) == S_IFREG, old.st_uid == geteuid(), old.st_nlink == 1,
                  old.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        } else if errno != ENOENT { throw Failure.unavailable }
        let parentURL = archive ? archiveDirectory : directory
        guard target.deletingLastPathComponent().standardizedFileURL.path == parentURL.standardizedFileURL.path else { throw Failure.invalid }
        if !archive { try checkRootAllocation(bytes, target: target) }
        var template = Array(parentURL.appendingPathComponent(".request-XXXXXX").path.utf8CString)
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
        let parent = open(parentURL.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard parent >= 0 else { throw Failure.installed }
        defer { close(parent) }
        guard syncParent(parent) else { throw Failure.installed }
    }
    private func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    private func decisionFile(_ scope: String) throws -> URL {
        _ = try file(scope)
        return directory.appendingPathComponent(scope + ".v1.decision")
    }
    private var archiveDirectory: URL { directory.appendingPathComponent("Archived", isDirectory: true) }
    private func archiveFile(_ review: ArchiveReview) -> URL {
        archiveDirectory.appendingPathComponent(review.scope + "." + review.cipherFingerprint + ".v1.aes")
    }
    private func prepareArchive(create: Bool = true) throws {
        if !FileManager.default.fileExists(atPath: archiveDirectory.path) {
            guard create else { throw Failure.unavailable }
            try FileManager.default.createDirectory(at: archiveDirectory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
            let parent = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard parent >= 0 else { throw Failure.unavailable }
            defer { close(parent) }
            guard syncParent(parent) else { throw Failure.unavailable }
        }
        var info = stat()
        guard lstat(archiveDirectory.path, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
              info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else { throw Failure.unavailable }
    }
    private func decisionAAD(_ scope: String) -> Data { Data(("vox-private-request-decision/v1|" + scope).utf8) }
    private func writeDecision(_ review: ArchiveReview, state: String) throws {
        _ = try file(review.scope)
        guard [review.cipherFingerprint, review.eventFingerprint].allSatisfy({ $0.count == 64 && $0.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) }),
              ["archived-unknown", "held-unknown"].contains(state) else { throw Failure.invalid }
        let value = Decision(version: 1, scope: review.scope, cipherFingerprint: review.cipherFingerprint,
                             eventFingerprint: review.eventFingerprint, state: state)
        let sealed = try AES.GCM.seal(JSONEncoder().encode(value), using: key(create: false), authenticating: decisionAAD(review.scope)).combined!
        try install(sealed, target: decisionFile(review.scope))
    }
    private func archived(scope: String, bytes: Data) throws -> Bool {
        let path = try decisionFile(scope)
        var info = stat()
        if lstat(path.path, &info) != 0 {
            guard errno == ENOENT else { throw Failure.unavailable }
            return false
        }
        let plain = try AES.GCM.open(AES.GCM.SealedBox(combined: fileData(path)), using: key(create: false), authenticating: decisionAAD(scope))
        guard let object = try JSONSerialization.jsonObject(with: plain) as? [String: Any],
              Set(object.keys) == Set(["version", "scope", "cipherFingerprint", "eventFingerprint", "state"]) else { throw Failure.invalid }
        let value = try JSONDecoder().decode(Decision.self, from: plain)
        guard value.version == 1, value.scope == scope, ["archived-unknown", "held-unknown"].contains(value.state),
              [value.cipherFingerprint, value.eventFingerprint].allSatisfy({ $0.count == 64 && $0.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) }) else { throw Failure.invalid }
        guard value.state == "archived-unknown", value.cipherFingerprint == digest(bytes) else { return false }
        try prepareArchive(create: false)
        let copy = try fileData(archiveFile(.init(scope: scope, cipherFingerprint: value.cipherFingerprint, eventFingerprint: value.eventFingerprint)))
        guard copy == bytes else { throw Failure.invalid }
        let parent = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard parent >= 0 else { throw Failure.unavailable }
        defer { close(parent) }
        guard syncParent(parent) else { throw Failure.unavailable }
        return true
    }
    func reviewArchive(scope: String, pending: ContextPrivateInbox.PendingWrite) throws -> ArchiveReview {
        lock.lock(); defer { lock.unlock() }
        try checkDirectory(create: false)
        let lease = try processLock(); defer { close(lease) }
        let record = try read(scope: scope)
        guard !record.confirmed, record.pending == pending else { throw Failure.invalid }
        return .init(scope: scope, cipherFingerprint: digest(try fileData(file(scope))),
                     eventFingerprint: eventFingerprint(pending))
    }
    /// Explicit decision only: preserve exact ciphertext, then persist the decision permitting a later overwrite.
    func archive(_ review: ArchiveReview, pending: ContextPrivateInbox.PendingWrite) throws {
        lock.lock(); defer { lock.unlock() }
        try checkDirectory(create: false)
        let lease = try processLock(); defer { close(lease) }
        let record = try read(scope: review.scope), bytes = try fileData(file(review.scope))
        guard !record.confirmed, record.pending == pending, digest(bytes) == review.cipherFingerprint,
              review.eventFingerprint == eventFingerprint(pending) else { throw Failure.invalid }
        if try archived(scope: review.scope, bytes: bytes) { return }
        try prepareArchive()
        let copy = archiveFile(review)
        if FileManager.default.fileExists(atPath: copy.path) {
            guard try fileData(copy) == bytes else { throw Failure.invalid }
        } else {
            let files = try boundedEntries(in: archiveDirectory)
            guard files.count < 256 else { throw Failure.capacity }
            let snapshots = files.filter { $0.lastPathComponent.range(of: #"^[a-f0-9]{64}\.[a-f0-9]{64}\.v1\.aes\z"#, options: .regularExpression) != nil }
            guard snapshots.count < 64 else { throw Failure.capacity }
            var size = 0
            for file in snapshots { size += try fileSize(file) }
            guard size + bytes.count <= 64_000_000 else { throw Failure.capacity }
            try install(bytes, target: copy, archive: true)
        }
        // Flush a preexisting exact copy too: it might originate from an earlier interrupted install.
        let parent = open(archiveDirectory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard parent >= 0 else { throw Failure.unavailable }
        defer { close(parent) }
        guard syncParent(parent) else { throw Failure.unavailable }
        try writeDecision(review, state: "archived-unknown")
    }
    /// Undo release only while the original active ciphertext is still present; never overwrite a newer request.
    func holdArchived(_ review: ArchiveReview) throws {
        lock.lock(); defer { lock.unlock() }
        try checkDirectory(create: false)
        let lease = try processLock(); defer { close(lease) }
        let record = try read(scope: review.scope), bytes = try fileData(file(review.scope))
        guard !record.confirmed, digest(bytes) == review.cipherFingerprint,
              review.eventFingerprint == eventFingerprint(record.pending),
              try archived(scope: review.scope, bytes: bytes) else { throw Failure.invalid }
        try writeDecision(review, state: "held-unknown")
    }
    func hasUnconfirmed(scope: String) throws -> Bool {
        _ = try file(scope)
        lock.lock(); defer { lock.unlock() }
        try validateDirectoryChain(allowMissing: true)
        var info = stat()
        if lstat(directory.path, &info) != 0 {
            guard errno == ENOENT else { throw Failure.unavailable }
            return false
        }
        try checkDirectory(create: false)
        let lease = try processLock(); defer { close(lease) }
        if lstat(try file(scope).path, &info) != 0 {
            guard errno == ENOENT else { throw Failure.unavailable }
            return false
        }
        return !(try read(scope: scope)).confirmed
    }
    /// Explicit, bounded metadata-only inspection. No key access, locks created,
    /// ciphertext reads, directory creation, receipt claims, restoration or writes.
    func listArchiveFiles(scope: String) throws -> [ArchiveFileInfo] {
        lock.lock(); defer { lock.unlock() }
        _ = try file(scope)
        try validateDirectoryChain(allowMissing: true)
        let root = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        if root < 0 {
            guard errno == ENOENT else { throw Failure.unavailable }
            return []
        }
        defer { close(root) }
        var info = stat()
        guard fstat(root, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
              info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        let archive = openat(root, "Archived", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        if archive < 0 {
            guard errno == ENOENT else { throw Failure.unavailable }
            return []
        }
        defer { close(archive) }
        guard fstat(archive, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
              info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        let scan = fcntl(archive, F_DUPFD_CLOEXEC, 0)
        guard scan >= 0 else { throw Failure.unavailable }
        guard let stream = fdopendir(scan) else { close(scan); throw Failure.unavailable }
        defer { closedir(stream) }
        var entries = 0, snapshots = 0, totalBytes = 0
        var result: [ArchiveFileInfo] = []
        var labels = Set<String>()
        while true {
            errno = 0
            guard let entry = readdir(stream) else {
                guard errno == 0 else { throw Failure.unavailable }
                return result.sorted { $0.id < $1.id }
            }
            let name = withUnsafePointer(to: &entry.pointee.d_name) {
                $0.withMemoryRebound(to: CChar.self, capacity: Int(MAXNAMLEN) + 1) { String(cString: $0) }
            }
            if name == "." || name == ".." { continue }
            entries += 1
            guard entries <= 256 else { throw Failure.capacity }
            guard name.range(of: #"^[a-f0-9]{64}\.[a-f0-9]{64}\.v1\.aes\z"#, options: .regularExpression) != nil else { continue }
            snapshots += 1
            guard snapshots <= 64 else { throw Failure.capacity }
            guard name.hasPrefix(scope + ".") else { continue }
            var item = stat()
            guard fstatat(archive, name, &item, AT_SYMLINK_NOFOLLOW) == 0,
                  (item.st_mode & S_IFMT) == S_IFREG, item.st_uid == geteuid(),
                  item.st_nlink == 1, item.st_mode & 0o077 == 0,
                  item.st_size > 0, item.st_size <= 2_000_000 else { throw Failure.unavailable }
            totalBytes += Int(item.st_size)
            guard totalBytes <= 64_000_000 else { throw Failure.capacity }
            let label = String(name.dropFirst(65).prefix(64))
            guard labels.insert(label).inserted else { throw Failure.unavailable }
            result.append(.init(id: label, bytes: Int(item.st_size)))
        }
    }
    enum ArchiveKeyState: String { case present, missing, invalid, unavailable }
    /// Explicit lookup only. Never creates/replaces a key or decrypts; strict Keychain may ask to unlock.
    func inspectArchiveKey(scope: String) -> ArchiveKeyState {
        do {
            guard !((try listArchiveFiles(scope: scope)).isEmpty) else { return .unavailable }
            let encoded: String?
            do { encoded = try readKey() } catch { return .unavailable }
            guard let encoded else { return .missing }
            guard let bytes = Data(base64Encoded: encoded), bytes.count == 32 else { return .invalid }
            return .present
        } catch { return .unavailable }
    }
    /// Explicit bounded ciphertext fingerprint check, not decryption, authenticity or delivery proof.
    func archiveFingerprintMatches(scope: String, label: String) throws -> Bool {
        digest(try archiveBytes(scope: scope, label: label)) == label
    }
    private func eventFingerprint(_ pending: ContextPrivateInbox.PendingWrite) -> String {
        digest(Data((pending.eventId + "|" + (pending.mutationId ?? "")).utf8))
    }
    struct AuthenticatedArchive: Equatable {
        let cipherFingerprint: String
        let eventFingerprint: String
        let confirmed: Bool // Saved local state, never a fresh server delivery receipt.
    }
    /// Explicit read-only authentication and schema check. Never restores, sends, creates a key or exposes plaintext.
    func inspectAuthenticatedArchive(scope: String, label: String) throws -> AuthenticatedArchive {
        let bytes = try archiveBytes(scope: scope, label: label)
        guard digest(bytes) == label else { throw Failure.invalid }
        let saved = try decode(bytes, scope: scope)
        return .init(cipherFingerprint: label, eventFingerprint: eventFingerprint(saved.pending), confirmed: saved.confirmed)
    }
    private func archiveBytes(scope: String, label: String) throws -> Data {
        guard label.range(of: "^[a-f0-9]{64}\\z", options: .regularExpression) != nil,
              try listArchiveFiles(scope: scope).contains(where: { $0.id == label }) else { throw Failure.invalid }
        lock.lock(); defer { lock.unlock() }
        try validateDirectoryChain(allowMissing: false)
        let root = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard root >= 0 else { throw Failure.unavailable }; defer { close(root) }
        var info = stat()
        guard fstat(root, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
              info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        let archive = openat(root, "Archived", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard archive >= 0 else { throw Failure.unavailable }; defer { close(archive) }
        guard fstat(archive, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
              info.st_uid == geteuid(), info.st_mode & 0o077 == 0 else { throw Failure.unavailable }
        let fd = openat(archive, scope + "." + label + ".v1.aes", O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard fd >= 0 else { throw Failure.unavailable }; defer { close(fd) }
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, info.st_uid == geteuid(),
              info.st_nlink == 1, info.st_mode & 0o077 == 0, info.st_size > 0, info.st_size <= 2_000_000 else { throw Failure.unavailable }
        let initial = info
        var bytes = Data(count: Int(info.st_size))
        let size = bytes.count
        try bytes.withUnsafeMutableBytes { buffer in
            var offset = 0
            while offset < size {
                let count = Darwin.read(fd, buffer.baseAddress!.advanced(by: offset), size - offset)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw Failure.unavailable }; offset += count
            }
        }
        guard fstat(fd, &info) == 0, info.st_size == initial.st_size,
              info.st_mtimespec.tv_sec == initial.st_mtimespec.tv_sec, info.st_mtimespec.tv_nsec == initial.st_mtimespec.tv_nsec,
              info.st_ctimespec.tv_sec == initial.st_ctimespec.tv_sec, info.st_ctimespec.tv_nsec == initial.st_ctimespec.tv_nsec else { throw Failure.unavailable }
        return bytes
    }
    func load(scope: String) throws -> ContextPrivateSavedRequest {
        lock.lock(); defer { lock.unlock() }
        _ = try file(scope)
        try checkDirectory(create: false)
        let lease = try processLock(); defer { close(lease) }
        return try read(scope: scope)
    }
    private func read(scope: String) throws -> ContextPrivateSavedRequest {
        try decode(fileData(file(scope)), scope: scope)
    }
    private func decode(_ data: Data, scope: String) throws -> ContextPrivateSavedRequest {
        let plain = try AES.GCM.open(AES.GCM.SealedBox(combined: data), using: key(create: false), authenticating: aad(scope))
        guard plain.count <= 2_000_000,
              let object = try JSONSerialization.jsonObject(with: plain) as? [String: Any],
              Set(object.keys) == Set(["version", "scope", "pending", "confirmed"]),
              let pending = object["pending"] as? [String: Any],
              Set(pending.keys).isSubset(of: Set(["path", "method", "data", "mutationId", "selected", "withdrawn", "eventId", "text", "tags", "projectId", "repositoryId", "inputEditorId"])),
              pending["selected"].map({ ($0 as? [String: Any]).map { Set($0.keys) == Set(["id", "eventId", "revision", "createdAt", "changedAt"]) } ?? false }) ?? true else { throw Failure.invalid }
        let draft = try JSONDecoder().decode(ContextPrivateSavedRequest.self, from: plain)
        guard draft.valid, draft.scope == scope else { throw Failure.invalid }
        return draft
    }
    private func fileSize(_ path: URL) throws -> Int {
        let descriptor = open(path.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard descriptor >= 0 else { throw Failure.unavailable }
        defer { close(descriptor) }
        var info = stat()
        guard fstat(descriptor, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_uid == geteuid(), info.st_nlink == 1, info.st_mode & 0o077 == 0,
              info.st_size > 0, info.st_size <= 2_000_000 else { throw Failure.unavailable }
        return Int(info.st_size)
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
