"""Exercise production actor exit cleanup; no live model, network, keys or user data."""

import argparse
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", required=True)
parser.add_argument("--launch-policy", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = root / ("build/asr-launch-policy" if args.launch_policy else "build/asr-exit-harness")
out.mkdir(parents=True, exist_ok=True)
services = root / "macos/VoxLog/Services"
# Concatenate without rewriting production implementation or its visibility.
(out / "Manager.swift").write_text(
    (services / "SenseVoiceServerManager.swift").read_text()
    + "\n"
    + (
        root
        / "macos/VoxLogTests"
        / ("ASRLaunchPolicyHarness.swift" if args.launch_policy else "ASRExitHarness.swift")
    ).read_text()
)
(out / "Stubs.swift").write_text("""import Foundation
func L(_ zh: String, _ en: String) -> String { en }
enum DebugFileLogger { static func log(_ text: String) {} }
enum HotwordStorage { static func loadEffective() -> [String] { fatalError("not in scope") } }
""")
names = [
    "ModelManager",
    "ASRPortReader",
    "ASRProcessRegistry",
    "ASRProcessOutputPolicy",
    "ASRComponentResolver",
    "ModelManifest",
    "ModelTreeVerifier",
    "ModelStore",
    "QwenModelCatalog",
]
command = [
    "swiftc",
    "-D",
    "ASR_LAUNCH_POLICY_HARNESS" if args.launch_policy else "ASR_EXIT_HARNESS",
    "-sdk",
    args.sdk,
    "-parse-as-library",
    str(out / "Manager.swift"),
    str(out / "Stubs.swift"),
    *[str(services / (name + ".swift")) for name in names],
    "-o",
    str(out / "verify"),
]
for flags in [[], ["-D", "DEBUG"]] if args.launch_policy else [[]]:
    subprocess.run(command + flags, check=True, timeout=90)
    subprocess.run([str(out / "verify")], check=True, timeout=30)
