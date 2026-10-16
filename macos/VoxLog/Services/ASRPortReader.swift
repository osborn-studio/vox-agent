import Foundation

/// Owns continuous stdout consumption, including after port discovery. No server text is logged.
final class ASRPortReader: @unchecked Sendable {
    private let handle: FileHandle
    private let lock = NSLock()
    private var line = Data()
    private var discarding = false
    private var resolved = false
    private var result: Int?
    private var waiter: CheckedContinuation<Int?, Never>?

    init(pipe: Pipe) {
        handle = pipe.fileHandleForReading
        handle.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            self?.receive(data)
        }
    }

    deinit { handle.readabilityHandler = nil }

    func waitForPort(timeout: TimeInterval) async -> Int? {
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                lock.lock()
                if resolved || Task.isCancelled {
                    let value = Task.isCancelled ? nil : result
                    lock.unlock()
                    continuation.resume(returning: value)
                    return
                }
                guard waiter == nil else {
                    lock.unlock()
                    continuation.resume(returning: nil)
                    return
                }
                waiter = continuation
                lock.unlock()
                DispatchQueue.global().asyncAfter(deadline: .now() + max(0, timeout)) { [weak self] in
                    self?.finish(nil, stopReading: true)
                }
            }
        } onCancel: { self.cancel() }
    }

    func cancel() { finish(nil, stopReading: true, forceStop: true) }

    private func receive(_ data: Data) {
        if data.isEmpty { finish(nil, stopReading: true, forceStop: true); return }
        lock.lock()
        guard !resolved else { lock.unlock(); return } // Bytes already read, so pipe remains drained.
        var found: Int?
        for byte in data {
            if byte == 10 {
                if !discarding {
                    if line.last == 13 { line.removeLast() }
                    if let text = String(data: line, encoding: .utf8), text.hasPrefix("PORT:") {
                        let digits = text.dropFirst(5)
                        if !digits.isEmpty, digits.utf8.allSatisfy({ (48...57).contains($0) }),
                           let port = Int(digits), (1...65535).contains(port) { found = port }
                    }
                }
                line.removeAll(keepingCapacity: true)
                discarding = false
                if found != nil { break }
            } else if !discarding {
                if line.count < 4096 { line.append(byte) }
                else { line.removeAll(keepingCapacity: true); discarding = true }
            }
        }
        var continuation: CheckedContinuation<Int?, Never>?
        if let found { resolved = true; result = found; continuation = waiter; waiter = nil }
        lock.unlock()
        continuation?.resume(returning: found)
    }

    private func finish(_ value: Int?, stopReading: Bool, forceStop: Bool = false) {
        lock.lock()
        // A discovery timeout must not stop draining a server which already announced its port.
        if resolved {
            lock.unlock()
            if forceStop { handle.readabilityHandler = nil }
            return
        }
        resolved = true
        result = value
        let continuation = waiter
        waiter = nil
        lock.unlock()
        if stopReading { handle.readabilityHandler = nil }
        continuation?.resume(returning: value)
    }
}
