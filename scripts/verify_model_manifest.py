"""Compile/run pure Swift model validation using CLT; no XCTest/network/user data."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk", required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = root / "build/model-manifest"
out.mkdir(parents=True, exist_ok=True)
subprocess.run(
    [
        "swiftc",
        "-D",
        "MODEL_MANIFEST_HARNESS",
        "-sdk",
        args.sdk,
        "-parse-as-library",
        "-module-cache-path",
        str(out / "module-cache"),
        str(root / "macos/VoxLog/Services/ModelManifest.swift"),
        str(root / "macos/VoxLog/Services/ModelTreeVerifier.swift"),
        str(root / "macos/VoxLog/Services/ModelStore.swift"),
        str(root / "macos/VoxLog/Services/QwenModelCatalog.swift"),
        str(root / "macos/VoxLog/Services/ASRComponentResolver.swift"),
        str(root / "macos/VoxLogTests/ModelManifestHarness.swift"),
        "-o",
        str(out / "verify"),
    ],
    check=True,
    timeout=90,
)
entries = [
    {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    for name, data in [("a/x", b"123"), ("B", b"xy"), ("a-b", b"m")]
]
canonical = json.dumps(
    sorted(entries, key=lambda e: e["path"]), sort_keys=True, separators=(",", ":")
).encode()
expected = hashlib.sha256(canonical).hexdigest()
subprocess.run([str(out / "verify"), expected], check=True, timeout=30, cwd=root)
