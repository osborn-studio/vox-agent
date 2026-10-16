import Foundation
import Darwin

/// Downloads only catalog bytes into an isolated temporary source, never publishes a model.
/// Hash verification is the trust anchor; mirror metadata and redirects are never trusted.
enum PinnedModelDownload {
    enum Failure: Error { case mirror, response, oversized, incomplete, alreadyInstalled, installedCheckpointCleanupUnconfirmed }
    typealias Transport = (URL, Int64, @escaping (Data) throws -> Void) async throws -> Void

    struct Progress: Equatable, Sendable {
        let receivedBytes: Int64
        let reusedBytes: Int64
        let verifiedBytes: Int64
        let totalBytes: Int64
        var valid: Bool {
            totalBytes >= 0 && totalBytes <= 16 * 1024 * 1024 * 1024
                && receivedBytes >= 0 && reusedBytes >= 0 && verifiedBytes >= 0
                && receivedBytes <= totalBytes && reusedBytes <= totalBytes - receivedBytes
                && verifiedBytes <= receivedBytes + reusedBytes
        }
    }
    typealias Reporter = @Sendable (Progress) -> Void

    static func validateMirror(_ mirror: URL) throws {
        guard mirror.scheme == "https", let host = mirror.host, !host.isEmpty,
              mirror.user == nil, mirror.password == nil, mirror.query == nil,
              mirror.fragment == nil else { throw Failure.mirror }
    }

    static func qwen(mirror: URL, parent: URL, progress: Reporter? = nil) async throws -> URL {
        let manifest = try QwenModelCatalog.manifest()
        if try installedModel(in: ASRComponentResolver.defaultRoot(applicationSupport: parent),
            modelID: QwenModelCatalog.modelID, manifest: manifest) { throw Failure.alreadyInstalled }
        let checkpoint = try VerifiedModelDownloadCache(parent: parent, mirror: mirror, manifest: manifest)
        return try await download(mirror: mirror, parent: parent, manifest: manifest, checkpoint: checkpoint, progress: progress,
            additionalBytes: ModelStore.requiredImportBytes(manifest: manifest, cachedHashes: []),
            transport: { try await stream(url: $0, maximum: $1, emit: $2) })
    }

    static func qwenAndImport(mirror: URL, parent: URL, progress: Reporter? = nil) async throws -> URL {
        try validateMirror(mirror)
        let manifest = try QwenModelCatalog.manifest()
        do {
            if let installed = try ASRComponentResolver.managedModelIfPresent(
                in: ASRComponentResolver.defaultRoot(applicationSupport: parent), modelID: QwenModelCatalog.modelID, manifest: manifest) {
                try await cleanupVerifiedCheckpoint(mirror: mirror, parent: parent, manifest: manifest)
                return installed
            }
        } catch ASRComponentResolver.Failure.missingModel { }
        let source = try await qwen(mirror: mirror, parent: parent, progress: progress)
        defer { try? FileManager.default.removeItem(at: source) }
        try Task.checkCancellation()
        let installed = try OfflineModelImporter.importQwen(source: source, applicationSupport: parent)
        do {
            try await cleanupVerifiedCheckpoint(mirror: mirror, parent: parent, manifest: manifest)
        } catch is CancellationError { throw CancellationError() }
          catch { throw Failure.installedCheckpointCleanupUnconfirmed }
        return installed
    }

    /// Call after verifying the installed model. Retry only transient cooperative leases.
    static func cleanupVerifiedCheckpoint(mirror: URL, parent: URL, manifest: ModelManifest) async throws {
        for attempt in 0..<20 {
            try Task.checkCancellation()
            do {
                let checkpoint = try VerifiedModelDownloadCache(parent: parent, mirror: mirror, manifest: manifest, existingOnly: true)
                try checkpoint.discardVerified(manifest: manifest)
                return
            } catch VerifiedModelDownloadCache.Failure.absent { return }
              catch VerifiedModelDownloadCache.Failure.busy {
                if attempt == 19 { throw VerifiedModelDownloadCache.Failure.busy }
                try await Task.sleep(nanoseconds: 25_000_000)
            }
        }
    }

    /// Absence of this revision in a safe existing store permits installation, never corrupt bytes.
    static func installedModel(in root: URL, modelID: String, manifest: ModelManifest) throws -> Bool {
        do { return try ASRComponentResolver.managedModelIfPresent(in: root, modelID: modelID, manifest: manifest) != nil }
        catch ASRComponentResolver.Failure.missingModel { return false }
    }

    static func download(mirror: URL, parent: URL, manifest: ModelManifest, checkpoint: VerifiedModelDownloadCache? = nil, progress: Reporter? = nil, additionalBytes: UInt64 = 0, transport: Transport) async throws -> URL {
        try validateMirror(mirror)
        try Task.checkCancellation()
        let fd = try ModelTreeVerifier.openRoot(parent)
        defer { close(fd) }
        var volume = statfs()
        guard fstatfs(fd, &volume) == 0, volume.f_bsize > 0,
              volume.f_bavail >= (UInt64(manifest.totalBytes) + additionalBytes + (checkpoint == nil ? 0 : UInt64(manifest.totalBytes)) + 64 * 1024 * 1024 + UInt64(volume.f_bsize) - 1) / UInt64(volume.f_bsize)
        else { throw ModelTreeVerifier.Failure.insufficientSpace }
        let directory = parent.appendingPathComponent(".qwen-download-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        var complete = false
        defer { if !complete { try? FileManager.default.removeItem(at: directory) } }
        var received: Int64 = 0, reused: Int64 = 0, verified: Int64 = 0
        var lastReportedBytes: Int64 = 0, lastReportedTime = ProcessInfo.processInfo.systemUptime
        func report(force: Bool = false) {
            let now = ProcessInfo.processInfo.systemUptime
            guard force || received + reused - lastReportedBytes >= 4 * 1024 * 1024 || now - lastReportedTime >= 0.25 else { return }
            progress?(.init(receivedBytes: received, reusedBytes: reused, verifiedBytes: verified, totalBytes: manifest.totalBytes))
            lastReportedBytes = received + reused; lastReportedTime = now
        }
        report(force: true)
        for entry in manifest.files {
            try Task.checkCancellation()
            let file = directory.appendingPathComponent(entry.path)
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let output = open(file.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
            guard output >= 0 else { throw ModelTreeVerifier.Failure.writeFailed }
            do {
                var count: Int64 = 0
                let reusing = try checkpoint?.reusableOrQuarantine(entry) ?? false
                let receive: (Data) throws -> Void = { data in
                    // Delegate emits outside a Swift task; Transfer cancellation serializes with writes.
                    try Task.checkCancellation()
                    guard Int64(data.count) <= entry.size - count else { throw Failure.oversized }
                    try data.withUnsafeBytes { buffer in
                        var offset = 0
                        while offset < data.count {
                            let written = write(output, buffer.baseAddress!.advanced(by: offset), data.count - offset)
                            if written < 0 && errno == EINTR { continue }
                            guard written > 0 else { throw ModelTreeVerifier.Failure.writeFailed }
                            offset += written
                        }
                    }
                    count += Int64(data.count)
                    if reusing { reused += Int64(data.count) } else { received += Int64(data.count) }
                    report()
                }
                if let checkpoint, reusing {
                    try checkpoint.emit(entry, to: receive)
                } else {
                    try await transport(mirror.appendingPathComponent(entry.path), entry.size, receive)
                }
                guard count == entry.size else { throw Failure.incomplete }
                guard fsync(output) == 0 else { throw ModelTreeVerifier.Failure.writeFailed }
                if let checkpoint { try checkpoint.retain(entry, from: directory) }
                else { try ModelTreeVerifier.verify(entry: entry, root: directory) }
                verified += entry.size; report(force: true)
                close(output)
            } catch {
                close(output)
                if Task.isCancelled { throw CancellationError() }
                throw error
            }
        }
        try Task.checkCancellation()
        try ModelTreeVerifier.verify(root: directory, manifest: manifest)
        complete = true
        return directory
    }

    private final class Transfer: NSObject, URLSessionDataDelegate, @unchecked Sendable {
        let url: URL
        let maximum: Int64
        let emit: (Data) throws -> Void
        let lock = NSLock()
        var continuation: CheckedContinuation<Void, Error>?
        var task: URLSessionDataTask?
        var cancelled = false, finished = false, received: Int64 = 0
        init(url: URL, maximum: Int64, emit: @escaping (Data) throws -> Void) {
            self.url = url; self.maximum = maximum; self.emit = emit
        }
        func start(_ session: URLSession, request: URLRequest, continuation: CheckedContinuation<Void, Error>) {
            lock.lock()
            if cancelled {
                finished = true; lock.unlock(); continuation.resume(throwing: CancellationError()); return
            }
            self.continuation = continuation
            let task = session.dataTask(with: request); self.task = task
            task.resume(); lock.unlock()
        }
        // Caller holds lock. Resume only after releasing it so cancelled writers cannot outlive await.
        func finish(_ error: Error?) {
            guard !finished else { lock.unlock(); return }
            finished = true
            let pending = continuation; continuation = nil
            task?.cancel(); task = nil
            lock.unlock()
            if let error { pending?.resume(throwing: error) } else { pending?.resume() }
        }
        func cancel() {
            lock.lock(); cancelled = true
            if continuation == nil { lock.unlock(); return }
            finish(CancellationError())
        }
        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
            lock.lock()
            guard !finished, let response = response as? HTTPURLResponse, response.statusCode == 200,
                  response.url == url, response.expectedContentLength < 0 || response.expectedContentLength == maximum,
                  response.value(forHTTPHeaderField: "Content-Encoding") == nil || response.value(forHTTPHeaderField: "Content-Encoding")?.lowercased() == "identity"
            else { finish(Failure.response); completionHandler(.cancel); return }
            lock.unlock(); completionHandler(.allow)
        }
        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
            lock.lock()
            guard !finished else { lock.unlock(); return }
            guard Int64(data.count) <= maximum - received else { finish(Failure.oversized); return }
            do { try emit(data); received += Int64(data.count); lock.unlock() }
            catch { finish(error) }
        }
        func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
            lock.lock(); finish(error)
        }
        func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
            lock.lock(); finish(Failure.response)
            completionHandler(nil)
        }
    }

    static func stream(url: URL, maximum: Int64, emit: @escaping (Data) throws -> Void, configuration: URLSessionConfiguration = .ephemeral) async throws {
        try Task.checkCancellation()
        let config = configuration
        config.urlCache = nil; config.httpCookieStorage = nil; config.urlCredentialStorage = nil
        config.timeoutIntervalForRequest = 60; config.timeoutIntervalForResource = 3600
        let transfer = Transfer(url: url, maximum: maximum, emit: emit)
        let session = URLSession(configuration: config, delegate: transfer, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        var request = URLRequest(url: url)
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if Task.isCancelled { continuation.resume(throwing: CancellationError()) }
                else { transfer.start(session, request: request, continuation: continuation) }
            }
        } onCancel: {
            // Do not make the main-actor Cancel button wait for an in-flight disk write.
            DispatchQueue.global(qos: .utility).async { transfer.cancel() }
        }
    }
}
