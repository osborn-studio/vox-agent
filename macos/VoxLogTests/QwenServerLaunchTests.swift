import XCTest
@testable import VoxLog

final class QwenServerLaunchTests: XCTestCase {
    func testBundledRuntimeWinsOverStaleDeveloperCheckout() throws {
        let launch = try SenseVoiceServerManager.resolveServerLaunch(
            bundledBinary: "/usr/bin/true", devDirectory: "/stale/checkout",
            pythonCandidates: ["/usr/bin/false"]
        )
        XCTAssertEqual(launch.executable, "/usr/bin/true")
        XCTAssertEqual(launch.script, "")
    }

    func testDeveloperBuildStillUsesPythonWhenNoBundleExists() throws {
        #if DEBUG
        let launch = try SenseVoiceServerManager.resolveServerLaunch(
            bundledBinary: nil, devDirectory: "/dev/server", pythonCandidates: ["/usr/bin/true"]
        )
        XCTAssertEqual(launch.executable, "/usr/bin/true")
        XCTAssertEqual(launch.script, "/dev/server/server.py")
        #else
        XCTAssertThrowsError(try SenseVoiceServerManager.resolveServerLaunch(
            bundledBinary: nil, devDirectory: "/dev/server", pythonCandidates: ["/usr/bin/true"]
        ))
        #endif
    }
}
