import Foundation

/// Local hints only. A remote URL never grants catalog membership or execution.
struct ContextRepositoryInspection: Equatable, Sendable {
    let repositories: [String]
    let branch: String?
}

enum ContextRepositoryResolver {
    enum InspectionError: Error { case unavailable, tooLarge }

    /// Accept standard HTTPS/SSH Git remotes, discard credentials and host paths.
    /// The returned slug is only a suggestion; the user must choose a catalog item.
    static func repositorySlug(_ remote: String) -> String? {
        guard remote == remote.trimmingCharacters(in: .whitespacesAndNewlines),
              !remote.isEmpty, remote.utf8.count <= 4096,
              !remote.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else { return nil }
        let path: String
        if remote.contains("://") {
            guard let parts = URLComponents(string: remote),
                  ["https", "ssh"].contains(parts.scheme ?? ""),
                  let host = parts.host, !host.isEmpty,
                  parts.query == nil, parts.fragment == nil,
                  !parts.percentEncodedPath.contains("%") else { return nil }
            path = parts.path.hasPrefix("/") ? String(parts.path.dropFirst()) : parts.path
        } else {
            guard let colon = remote.firstIndex(of: ":"), !remote.hasPrefix("/"), !remote.hasPrefix(".") else { return nil }
            let hostPart = remote[..<colon]
            guard !hostPart.contains("/"), hostPart.contains("@"), !hostPart.hasSuffix("@"),
                  hostPart.filter({ $0 == "@" }).count == 1 else { return nil }
            path = String(remote[remote.index(after: colon)...])
        }
        let slug = path.hasSuffix(".git") ? String(path.dropLast(4)) : path
        guard slug.utf8.count <= 200,
              slug.range(of: "^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$", options: .regularExpression) == slug.startIndex..<slug.endIndex else { return nil }
        return slug
    }

    static func parseRemotes(_ data: Data) throws -> [String] {
        guard data.count <= 65536, let raw = String(data: data, encoding: .utf8) else { throw InspectionError.tooLarge }
        // git config --null --get-regexp: key LF value NUL, not shell text.
        var values = Set<String>()
        for entry in raw.split(separator: "\0") {
            guard let newline = entry.firstIndex(of: "\n") else { continue }
            let key = entry[..<newline]
            guard key.hasPrefix("remote."), key.hasSuffix(".url") else { continue }
            if let slug = repositorySlug(String(entry[entry.index(after: newline)...])) { values.insert(slug) }
        }
        return values.sorted()
    }

    static func inspect(directory: URL) async throws -> ContextRepositoryInspection {
        try await Task.detached(priority: .utility) {
            let directory = directory.resolvingSymlinksInPath()
            var isDirectory: ObjCBool = false
            guard directory.isFileURL, FileManager.default.fileExists(atPath: directory.path, isDirectory: &isDirectory), isDirectory.boolValue,
                  !directory.pathComponents.contains(where: { $0.hasSuffix(".app") }) else { throw InspectionError.unavailable }
            let remotes = try runGit(directory: directory, arguments: ["config", "--local", "--no-includes", "--null", "--get-regexp", "^remote\\..*\\.url$"], allowedExit: [0, 1])
            let branchData = try runGit(directory: directory, arguments: ["symbolic-ref", "--short", "-q", "HEAD"], allowedExit: [0, 1])
            let branchText = String(data: branchData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            let branch = branchText.flatMap { value in
                !value.isEmpty && value.utf8.count <= 256 && value.unicodeScalars.allSatisfy({ !CharacterSet.controlCharacters.contains($0) }) ? value : nil
            }
            return ContextRepositoryInspection(repositories: try parseRemotes(remotes), branch: branch)
        }.value
    }

    private static func runGit(directory: URL, arguments: [String], allowedExit: Set<Int32>) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = ["-C", directory.path] + arguments
        // Ignore inherited Git redirection/config injection and disable prompting.
        process.environment = ["PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        try process.run()
        let timeout = DispatchWorkItem {
            if process.isRunning { process.terminate() }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 2, execute: timeout)
        defer { timeout.cancel(); try? output.fileHandleForReading.close() }
        var result = Data()
        while let chunk = try output.fileHandleForReading.read(upToCount: 4096), !chunk.isEmpty {
            result.append(chunk)
            if result.count > 65536 {
                if process.isRunning { process.terminate() }
                throw InspectionError.tooLarge
            }
        }
        process.waitUntilExit()
        guard allowedExit.contains(process.terminationStatus), process.terminationReason == .exit else { throw InspectionError.unavailable }
        return result
    }
}
