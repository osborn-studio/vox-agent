import XCTest
@testable import VoxLog

final class ModelManagerQwen3PathTests: XCTestCase {
    func testQwen3ServerPathsIncludeCurrentWorkspaceLocation() {
        let home = "/Users/tester"
        XCTAssertTrue(
            ModelManager.qwen3ServerScriptPaths(home: home)
                .contains("/Users/tester/Documents/VOXLOG & VOXAGENT/macos/qwen3-asr-server/server.py")
        )
    }

    func testQwen3ServerDirectoriesAreDerivedFromScriptPaths() {
        let home = "/Users/tester"
        XCTAssertEqual(
            ModelManager.qwen3ServerDirectories(home: home),
            ModelManager.qwen3ServerScriptPaths(home: home).map {
                ($0 as NSString).deletingLastPathComponent
            }
        )
    }

    func testQwen3PythonPathsIncludePortableVoxLogVirtualenv() {
        let home = "/Users/tester"
        XCTAssertTrue(ModelManager.qwen3PythonPaths(home: home).contains("/Users/tester/.voxlog-venv/bin/python"))
    }
}
