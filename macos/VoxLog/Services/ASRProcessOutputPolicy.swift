import Foundation

/// Helper output is untrusted; only structured native lifecycle diagnostics are persisted.
enum ASRProcessOutputPolicy {
    static func suppressRawDiagnostics(of process: Process) {
        process.standardError = FileHandle.nullDevice
    }
}
