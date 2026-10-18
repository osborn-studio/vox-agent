"""Offline, timeout-bounded production AccountClient harness; no GUI or real Keychain."""

import argparse
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", required=True)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = args.output or root / "build/phase3-loop/repair"
out.mkdir(parents=True, exist_ok=True)
stubs = """import Foundation
func L(_ zh: String, _ en: String) -> String { en }
enum KeychainService {
 static func load(key: String) -> String? { fatalError("real credential read") }
 static func loadScalarWithoutInteraction(key: String) -> String? { fatalError("real credential read") }
 static func save(key: String, value: String) throws { fatalError("real credential write") }
 static func delete(key: String) -> Bool { fatalError("real credential delete") }
}
@MainActor enum LoginSession {
 static func resetLogin() { fatalError("uninjected login reset") }
 static func completeFeishuLogin(orgID: String, name: String) { fatalError("uninjected login completion") }
}
"""
(out / "account-stubs.swift").write_text(stubs)
subprocess.run(
    [
        "swiftc",
        "-D",
        "FEISHU_ACCOUNT_HARNESS",
        "-sdk",
        args.sdk,
        "-parse-as-library",
        "-module-cache-path",
        str(out / "module-cache"),
        *[
            str(root / p)
            for p in [
                "macos/VoxLog/Services/InputBackupClient.swift",
                "macos/VoxLog/UI/Chat/SidebarSelection.swift",
                "macos/VoxLog/Services/FeishuConversationStore.swift",
                "macos/VoxLog/Services/FeishuAccountClient.swift",
                "macos/VoxLog/Services/FeishuTimelineModel.swift",
                "macos/VoxLog/Services/FeishuConversationOutbox.swift",
                "macos/VoxLogTests/FeishuAccountHarness.swift",
            ]
        ],
        str(out / "account-stubs.swift"),
        "-o",
        str(out / "account-tests"),
    ],
    check=True,
    timeout=90,
)
subprocess.run([str(out / "account-tests"), str(out)], check=True, timeout=30)
