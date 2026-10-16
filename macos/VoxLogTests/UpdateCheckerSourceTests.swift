import XCTest
@testable import VoxLog

final class UpdateCheckerSourceTests: XCTestCase {
    func testUpdateManifestAcceptsThe410ReleaseAndResolvesItsAsset() throws {
        let data = Data(
            """
            {
              "latest": "4.1.0",
              "releases": [
                {
                  "version": "4.1.0",
                  "date": "2026-07-26",
                  "notes": "Vox Agent 4.1.0"
                }
              ]
            }
            """.utf8
        )

        let manifest = try JSONDecoder().decode(UpdateManifest.self, from: data)

        XCTAssertEqual(manifest.latest, "4.1.0")
        XCTAssertEqual(manifest.releases.map(\.version), ["4.1.0"])
        XCTAssertEqual(
            manifest.releases[0].resolvedDmgURL.absoluteString,
            "https://github.com/gilosbornhello/vox-agents/releases/download/v4.1.0/VoxAgent-v4.1.0-cloud.dmg"
        )
    }

    func testUpdateCheckerUsesVoxAgentsGitHubReleaseManifest() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/Services/UpdateChecker.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("gilosbornhello/vox-agents/releases/latest/download/voxagent-updates.json"))
        XCTAssertTrue(source.contains("gilosbornhello/vox-agents/releases/download/v\\(version)/VoxAgent-v\\(version)-cloud.dmg"))
        XCTAssertTrue(source.contains("fetchManifestWithGitHubCLI()"))
        XCTAssertTrue(source.contains("runGitHubCLI"))
        XCTAssertFalse(source.contains("disabled-updates.json"))
        XCTAssertFalse(source.contains("gilosbornhello/voxlog/releases"))
    }

    func testAppUpdaterStoresDownloadedVoxAgentDmgName() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/Services/AppUpdater.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("VoxAgent-v\\(version)-cloud.dmg"))
        XCTAssertTrue(source.contains("startGitHubCLIDownload(release: release)"))
        XCTAssertTrue(source.contains("private func startGitHubCLIDownload(release: UpdateInfo)"))
        XCTAssertTrue(source.contains("gh is installed and authenticated"))
        XCTAssertFalse(source.contains("VoxLog-v\\(version)-cloud.dmg"))
    }

    func testAboutTabLinksToCurrentVoxAgentsRepository() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("VoxLog/UI/Settings/AboutTab.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        XCTAssertTrue(source.contains("https://github.com/gilosbornhello/vox-agents"))
        XCTAssertTrue(source.contains("https://github.com/gilosbornhello/vox-agents/issues"))
        XCTAssertTrue(source.contains("https://github.com/gilosbornhello/vox-agents/releases"))
        XCTAssertFalse(source.contains("https://github.com/gilosbornhello/voxlog"))
    }
}
