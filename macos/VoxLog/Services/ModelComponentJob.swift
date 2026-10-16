import Foundation
import Combine

/// Process-lifetime owner: settings recreation never loses an in-flight component job or Cancel.
@MainActor final class ModelComponentJob: ObservableObject {
    static let shared = ModelComponentJob()
    @Published private(set) var busy = false
    @Published private(set) var downloading = false
    @Published var status = ""
    @Published private(set) var cancelling = false
    @Published private(set) var completionRevision = 0
    @Published private(set) var downloadProgress: PinnedModelDownload.Progress?
    private var progressToken = UUID()
    private var worker: Task<URL, Error>?

    @discardableResult
    func start(downloading: Bool, success: String, failure: @escaping (Error) -> String,
               work: @escaping @Sendable () async throws -> URL) -> Bool {
        guard !busy else { return false }
        progressToken = UUID(); downloadProgress = nil
        busy = true; self.downloading = downloading; cancelling = false; status = ""
        let worker = Task.detached(priority: .utility, operation: work)
        self.worker = worker
        Task {
            do { _ = try await worker.value; status = success }
            catch { status = failure(error) }
            progressToken = UUID(); downloadProgress = nil
            self.worker = nil; busy = false; self.downloading = false
            cancelling = false; completionRevision += 1
        }
        return true
    }

    private final class ProgressMailbox: @unchecked Sendable {
        private let lock = NSLock()
        private var latest: PinnedModelDownload.Progress?
        private var scheduled = false
        func offer(_ snapshot: PinnedModelDownload.Progress) -> Bool {
            guard snapshot.valid else { return false }
            lock.lock(); defer { lock.unlock() }
            if let latest {
                guard latest.totalBytes == snapshot.totalBytes, snapshot.receivedBytes >= latest.receivedBytes,
                      snapshot.reusedBytes >= latest.reusedBytes, snapshot.verifiedBytes >= latest.verifiedBytes else { return false }
            }
            latest = snapshot
            if scheduled { return false }
            scheduled = true; return true
        }
        func take() -> PinnedModelDownload.Progress? {
            lock.lock(); defer { lock.unlock() }
            let snapshot = latest; latest = nil; scheduled = false
            return snapshot
        }
    }

    @discardableResult
    func startDownload(success: String, failure: @escaping (Error) -> String,
                       work: @escaping @Sendable (@escaping PinnedModelDownload.Reporter) async throws -> URL) -> Bool {
        let token = UUID(), mailbox = ProgressMailbox()
        let report: PinnedModelDownload.Reporter = { [weak self] snapshot in
            guard mailbox.offer(snapshot) else { return }
            Task { @MainActor [weak self] in
                guard let snapshot = mailbox.take(), let self, self.busy, self.downloading, self.progressToken == token else { return }
                if let old = self.downloadProgress {
                    guard old.totalBytes == snapshot.totalBytes, snapshot.receivedBytes >= old.receivedBytes,
                          snapshot.reusedBytes >= old.reusedBytes, snapshot.verifiedBytes >= old.verifiedBytes else { return }
                }
                self.downloadProgress = snapshot
            }
        }
        let started = start(downloading: true, success: success, failure: failure) { try await work(report) }
        if started { progressToken = token }
        return started
    }

    func cancel(status: String) {
        guard busy else { return }
        cancelling = true; self.status = status; worker?.cancel()
    }
}
