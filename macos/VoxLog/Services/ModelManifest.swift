import CryptoKit
import Foundation

/// A manifest is supplied by the App's trusted catalog, never by a download server.
/// The expected digest is an integrity check, not a signature or trust source.
/// This type validates metadata; successful construction does not verify model bytes.
struct ModelManifest: Sendable {
    struct Entry: Equatable, Sendable {
        let path: String
        let size: Int64
        let sha256: String
    }

    enum Failure: Error, Equatable {
        case invalidPath, invalidSize, invalidHash, collision, empty, tooLarge, digestMismatch
    }

    let files: [Entry]
    let digest: String
    let totalBytes: Int64

    init(files: [Entry], expectedDigest: String) throws {
        guard !files.isEmpty else { throw Failure.empty }
        guard files.count <= 4096 else { throw Failure.tooLarge }
        guard Self.isHash(expectedDigest) else { throw Failure.invalidHash }
        var paths = Set<String>()
        var prefixes: [String: String] = [:]
        var total: Int64 = 0
        for file in files {
            guard Self.safePath(file.path) else { throw Failure.invalidPath }
            guard file.size >= 0, file.size <= 8 * 1024 * 1024 * 1024 else {
                throw Failure.invalidSize
            }
            guard Self.isHash(file.sha256) else { throw Failure.invalidHash }
            guard paths.insert(file.path.lowercased()).inserted else { throw Failure.collision }
            total += file.size // bounded by 4096 entries, each <= 8 GiB
            guard total <= 16 * 1024 * 1024 * 1024 else { throw Failure.tooLarge }
            var prefix = ""
            for part in file.path.split(separator: "/") {
                prefix += prefix.isEmpty ? String(part) : "/" + part
                if let existing = prefixes[prefix.lowercased()], existing != prefix {
                    throw Failure.collision
                }
                prefixes[prefix.lowercased()] = prefix
            }
        }
        for path in paths {
            let parts = path.split(separator: "/")
            for count in 1..<parts.count {
                guard !paths.contains(parts.prefix(count).joined(separator: "/")) else {
                    throw Failure.collision
                }
            }
        }
        let sorted = files.sorted { $0.path.utf8.lexicographicallyPrecedes($1.path.utf8) }
        // Validated ASCII names and lower-case hex cannot contain JSON escaping characters.
        let canonical = "[" + sorted.map {
            "{\"path\":\"\($0.path)\",\"sha256\":\"\($0.sha256)\",\"size\":\($0.size)}"
        }.joined(separator: ",") + "]"
        let digest = Self.hash(Data(canonical.utf8))
        guard digest == expectedDigest else { throw Failure.digestMismatch }
        self.files = sorted
        self.digest = digest
        self.totalBytes = total
    }

    static func hash(_ bytes: Data) -> String {
        SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    }

    private static func isHash(_ value: String) -> Bool {
        value.utf8.count == 64 && value.utf8.allSatisfy {
            (48...57).contains($0) || (97...102).contains($0)
        }
    }

    private static func safePath(_ value: String) -> Bool {
        guard !value.isEmpty, value.utf8.count <= 1024,
              value.utf8.allSatisfy({
                  (48...57).contains($0) || (65...90).contains($0) ||
                  (97...122).contains($0) || [45, 46, 47, 95].contains($0)
              }) else { return false }
        return value.split(separator: "/", omittingEmptySubsequences: false).allSatisfy {
            !$0.isEmpty && $0 != "." && $0 != ".." && !$0.hasSuffix(".") && $0.utf8.count <= 255
        }
    }
}
