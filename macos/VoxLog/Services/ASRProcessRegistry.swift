import Foundation

/// Tracks actual child Process objects, never treats a persisted PID as authority.
final class ASRProcessRegistry: @unchecked Sendable {
    private let lock = NSLock()
    private var children: [ObjectIdentifier: Process] = [:]
    private var shuttingDown = false

    func insert(_ process: Process) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard !shuttingDown else { return false }
        children[ObjectIdentifier(process)] = process
        return true
    }

    func remove(_ process: Process) {
        lock.lock()
        children.removeValue(forKey: ObjectIdentifier(process))
        lock.unlock()
    }

    func terminateAll() {
        lock.lock()
        shuttingDown = true
        let owned = Array(children.values)
        children.removeAll()
        lock.unlock()
        for process in owned where process.isRunning { process.terminate() }
    }
}
