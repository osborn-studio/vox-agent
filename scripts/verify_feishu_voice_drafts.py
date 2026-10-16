"""Offline native harness for frozen Feishu voice draft ownership (no audio/network)."""

import argparse
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", required=True)
parser.add_argument("--output", type=Path, default=Path("build/phase6-loop/native"))
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
args.output.mkdir(parents=True, exist_ok=True)
exe = args.output.resolve() / "voice-tests"
subprocess.run(
    [
        "swiftc",
        "-D",
        "FEISHU_VOICE_HARNESS",
        "-sdk",
        args.sdk,
        "-parse-as-library",
        "-module-cache-path",
        str(args.output.resolve() / "module-cache"),
        str(root / "macos/VoxLog/UI/Chat/SidebarSelection.swift"),
        str(root / "macos/VoxLog/Services/FeishuConversationOutbox.swift"),
        str(root / "macos/VoxLogTests/FeishuVoiceDraftHarness.swift"),
        "-o",
        str(exe),
    ],
    check=True,
    timeout=90,
)
subprocess.run([str(exe)], check=True, timeout=30)
