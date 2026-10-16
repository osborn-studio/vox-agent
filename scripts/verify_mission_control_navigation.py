"""Run navigation assertions without XCTest on Command Line Tools-only hosts.

Compiles production navigation/model declarations with the unchanged behavioral
methods from MissionControlVisibilityTests. Only XCTest assertion functions are
substituted; this is NOT an XCTest or GUI pass. Use --sdk for an installed SDK.
"""

from pathlib import Path
import argparse
import json
import re
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", required=True)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = args.output or root / "build/phase1-verification"
out.mkdir(parents=True, exist_ok=True)
view = (root / "macos/VoxLog/UI/Chat/MissionControlView.swift").read_text()
event = (root / "macos/VoxLog/Services/EngineEventHandler.swift").read_text()
tests = (root / "macos/VoxLogTests/MissionControlVisibilityTests.swift").read_text()
tests = tests.replace("import XCTest", "").replace("@testable import VoxLog", "")
tests = tests.replace(
    "#filePath", json.dumps(str(root / "macos/VoxLogTests/MissionControlVisibilityTests.swift"))
)
shim = """import Foundation
class XCTestCase {}
func L(_ zh: String, _ en: String) -> String { en }
func XCTAssertTrue(_ value: Bool) { precondition(value) }
func XCTAssertFalse(_ value: Bool) { precondition(!value) }
func XCTAssertNil<T>(_ value: T?) { precondition(value == nil) }
func XCTAssertEqual<T: Equatable>(_ lhs: T, _ rhs: T) { precondition(lhs == rhs, "\\(lhs) != \\(rhs)") }
"""
workspace = view[view.index("enum AgentWorkspace:") : view.index("struct MissionControlModel")]
approval = event[
    event.index("struct PermissionApproval:") : event.index("struct AgentNotification:")
]
methods = re.findall(r"    func (test\w+)\(\)( throws)?", tests)
if not methods:
    raise RuntimeError("No navigation test methods found")
source = shim + workspace + approval + tests
source += "\nlet suite = MissionControlVisibilityTests()\n"
source += "\n".join(f"{'try ' if throws else ''}suite.{name}()" for name, throws in methods)
source += f'\nprint("PASS: {len(methods)} navigation scenarios (standalone assertion harness, not XCTest)")\n'
(out / "main.swift").write_text(source)
subprocess.run(
    [
        "swiftc",
        "-module-cache-path", str(out / "module-cache"),
        "-sdk",
        args.sdk,
        str(root / "macos/VoxLog/UI/Chat/MissionControlNavigation.swift"),
        str(out / "main.swift"),
        "-o",
        str(out / "navigation-tests"),
    ],
    check=True,
)
subprocess.run([str(out / "navigation-tests")], check=True)
