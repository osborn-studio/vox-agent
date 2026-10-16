"""Compile and exercise real subprocess stdout without ASR models or network."""

import argparse
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = root / "build/asr-port-reader"
out.mkdir(parents=True, exist_ok=True)
subprocess.run(
    [
        "swiftc",
        "-D",
        "ASR_PORT_READER_HARNESS",
        "-sdk",
        args.sdk,
        "-parse-as-library",
        str(root / "macos/VoxLog/Services/ASRPortReader.swift"),
        str(root / "macos/VoxLog/Services/ASRProcessRegistry.swift"),
        str(root / "macos/VoxLog/Services/ASRProcessOutputPolicy.swift"),
        str(root / "macos/VoxLogTests/ASRPortReaderHarness.swift"),
        "-o",
        str(out / "verify"),
    ],
    check=True,
    timeout=90,
)
subprocess.run([str(out / "verify")], check=True, timeout=20)
