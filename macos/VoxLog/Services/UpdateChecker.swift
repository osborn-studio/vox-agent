import Foundation

// MARK: - Models

struct UpdateInfo: Codable, Identifiable {
    let version: String
    let date: String
    let notes: String
    let cloudDmgURL: String?
    let cloudDmgSize: Int64?
    let cloudDmgSHA256: String?

    var id: String { version }

    enum CodingKeys: String, CodingKey {
        case version, date, notes
        case cloudDmgURL = "cloud_dmg_url"
        case cloudDmgSize = "cloud_dmg_size"
        case cloudDmgSHA256 = "cloud_dmg_sha256"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decode(String.self, forKey: .version)
        date = try c.decode(String.self, forKey: .date)
        notes = try c.decode(String.self, forKey: .notes)
        cloudDmgURL = try c.decodeIfPresent(String.self, forKey: .cloudDmgURL)
        cloudDmgSize = try c.decodeIfPresent(Int64.self, forKey: .cloudDmgSize)
        cloudDmgSHA256 = try c.decodeIfPresent(String.self, forKey: .cloudDmgSHA256)
    }

    /// Resolved DMG download URL (explicit or fallback from version)
    var resolvedDmgURL: URL {
        if let urlStr = cloudDmgURL, let url = URL(string: urlStr) { return url }
        return URL(string: "https://github.com/gilosbornhello/vox-agents/releases/download/v\(version)/VoxAgent-v\(version)-cloud.dmg")!
    }

    /// Human-readable download size (e.g. "23.5 MB")
    var formattedSize: String? {
        guard let size = cloudDmgSize else { return nil }
        let formatter = ByteCountFormatter()
        formatter.countStyle = .file
        return formatter.string(fromByteCount: size)
    }
}

struct UpdateManifest: Codable {
    let latest: String
    let releases: [UpdateInfo]
}

// MARK: - Update Checker

@MainActor
final class UpdateChecker {

    static let shared = UpdateChecker()

    private let repo = "gilosbornhello/vox-agents"
    private let url = URL(string: "https://github.com/gilosbornhello/vox-agents/releases/latest/download/voxagent-updates.json")!
    private let checkIntervalKey = "tf_lastUpdateCheck"
    private let seenVersionKey = "tf_lastSeenVersion"
    private let checkInterval: TimeInterval = 24 * 60 * 60 // 24 hours
    private var timer: Timer?

    private init() {}

    // MARK: - Public

    /// Start periodic checking: immediate check + 24h timer.
    func startPeriodicChecking(appState: AppState) {
        Task {
            await check(appState: appState)
        }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: checkInterval, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                await self.check(appState: appState)
            }
        }
    }

    /// Manual check (always fetches, ignores cooldown).
    func checkNow(appState: AppState) async {
        await fetch(appState: appState)
    }

    /// Mark the latest available version as "seen" so the red badge clears.
    func markAsSeen(appState: AppState) {
        guard let latest = appState.availableUpdates.first else { return }
        UserDefaults.standard.set(latest.version, forKey: seenVersionKey)
        appState.hasUnseenUpdate = false
    }

    var lastSeenVersion: String {
        UserDefaults.standard.string(forKey: seenVersionKey) ?? currentVersion
    }

    // MARK: - Private

    private var currentVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
    }

    /// Check with 24h cooldown.
    private func check(appState: AppState) async {
        let lastCheck = UserDefaults.standard.double(forKey: checkIntervalKey)
        let now = Date().timeIntervalSince1970
        if lastCheck > 0 && (now - lastCheck) < checkInterval {
            return
        }
        await fetch(appState: appState)
    }

    private func fetch(appState: AppState) async {
        appState.isCheckingUpdate = true
        defer { appState.isCheckingUpdate = false }

        do {
            let data = try await fetchManifestData()
            let manifest = try JSONDecoder().decode(UpdateManifest.self, from: data)
            UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: checkIntervalKey)

            let current = currentVersion
            let newer = manifest.releases
                .filter { compareVersions($0.version, isGreaterThan: current) }
                .sorted { compareVersions($0.version, isGreaterThan: $1.version) }

            appState.availableUpdates = newer
            appState.lastUpdateCheck = Date()

            if let latest = newer.first {
                appState.hasUnseenUpdate = compareVersions(latest.version, isGreaterThan: lastSeenVersion)
            } else {
                appState.hasUnseenUpdate = false
            }
        } catch {
            NSLog("[UpdateChecker] fetch failed: \(error)")
        }
    }

    private func fetchManifestData() async throws -> Data {
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
                throw URLError(.badServerResponse)
            }
            return data
        } catch {
            NSLog("[UpdateChecker] public manifest fetch failed, trying gh CLI: \(error)")
            return try await fetchManifestWithGitHubCLI()
        }
    }

    private func fetchManifestWithGitHubCLI() async throws -> Data {
        try await Task.detached(priority: .utility) { [repo] in
            let tempDir = FileManager.default.temporaryDirectory
                .appendingPathComponent("voxagent-update-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
            defer { try? FileManager.default.removeItem(at: tempDir) }

            let tag = try Self.runGitHubCLI([
                "release", "view",
                "--repo", repo,
                "--json", "tagName",
                "--jq", ".tagName",
            ]).trimmingCharacters(in: .whitespacesAndNewlines)

            guard !tag.isEmpty else {
                throw URLError(.cannotParseResponse)
            }

            _ = try Self.runGitHubCLI([
                "release", "download", tag,
                "--repo", repo,
                "--pattern", "voxagent-updates.json",
                "--dir", tempDir.path,
                "--clobber",
            ])

            return try Data(contentsOf: tempDir.appendingPathComponent("voxagent-updates.json"))
        }.value
    }

    nonisolated static func runGitHubCLI(_ arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["gh"] + arguments
        process.environment = [
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        ]

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        try process.run()
        process.waitUntilExit()

        let output = String(data: stdout.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let errorOutput = String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        guard process.terminationStatus == 0 else {
            throw NSError(
                domain: "VoxAgentGitHubCLI",
                code: Int(process.terminationStatus),
                userInfo: [NSLocalizedDescriptionKey: errorOutput.isEmpty ? "gh failed" : errorOutput]
            )
        }
        return output
    }

    /// Semantic version comparison: "1.2.0" > "1.1.0"
    private func compareVersions(_ a: String, isGreaterThan b: String) -> Bool {
        let partsA = a.split(separator: ".").compactMap { Int($0) }
        let partsB = b.split(separator: ".").compactMap { Int($0) }
        let count = max(partsA.count, partsB.count)
        for i in 0..<count {
            let va = i < partsA.count ? partsA[i] : 0
            let vb = i < partsB.count ? partsB[i] : 0
            if va != vb { return va > vb }
        }
        return false
    }
}
