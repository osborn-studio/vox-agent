"""Compile production pure navigation and run XCTest behavioral methods with a CLT shim.
No application launch, Keychain access, or network. This is not an XCTest/GUI run.
"""

import argparse
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", required=True)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = args.output or root / "build/phase3-loop"
out.mkdir(parents=True, exist_ok=True)
tests = (root / "macos/VoxLogTests/FeishuConversationNavigationTests.swift").read_text()
tests = tests.replace("import XCTest", "").replace("@testable import VoxLog", "")
methods = re.findall(r"    func (test\w+)\(\)( async)?", tests)
shim = """import Foundation
class XCTestCase {}
func XCTAssertTrue(_ value: Bool) { precondition(value) }
func XCTAssertFalse(_ value: Bool) { precondition(!value) }
func XCTAssertEqual<T: Equatable>(_ lhs: T, _ rhs: T) { precondition(lhs == rhs, "\\(lhs) != \\(rhs)") }
"""
source = (
    shim
    + tests
    + "\n@main struct Harness { @MainActor static func main() async {\nlet suite = FeishuConversationNavigationTests()\n"
)
source += "\n".join(
    f"{'await ' if asynchronous else ''}suite.{method}()" for method, asynchronous in methods
)
source += f'\nprint("PASS: {len(methods)} behavioral navigation scenarios (standalone, not XCTest)")\n}}}}\n'
(out / "navigation-main.swift").write_text(source)
subprocess.run(
    [
        "swiftc",
        "-sdk",
        args.sdk,
        "-parse-as-library",
        "-module-cache-path",
        str(out / "module-cache"),
        str(root / "macos/VoxLog/Agent/AgentModels.swift"),
        str(root / "macos/VoxLog/UI/Chat/SidebarSelection.swift"),
        str(root / "macos/VoxLog/Services/FeishuConversationStore.swift"),
        str(out / "navigation-main.swift"),
        "-o",
        str(out / "navigation-tests"),
    ],
    check=True,
    timeout=90,
)
subprocess.run([str(out / "navigation-tests")], check=True, timeout=30)
