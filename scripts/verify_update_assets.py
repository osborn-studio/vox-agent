#!/usr/bin/env python3
"""Fail closed before publishing: legacy in-app updater feeds are retired."""

import argparse
import hashlib
import os
import tempfile
from pathlib import Path
import re

LEGACY_FEED = "voxagent-updates.json"


def verify(directory: Path, version: str) -> list[Path]:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected x.y.z version")
    if (directory / LEGACY_FEED).exists() or (directory / LEGACY_FEED).is_symlink():
        raise ValueError("Legacy updater manifest must not be published")
    dmg = directory / f"VoxAgent-v{version}-cloud.dmg"
    if dmg.is_symlink() or not dmg.is_file() or dmg.stat().st_size == 0:
        raise ValueError("Missing regular cloud DMG")
    return [dmg]


def checksum(directory: Path, version: str) -> None:
    (dmg,) = verify(directory, version)
    with dmg.open("rb") as source:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
        digest = hasher.hexdigest()
    output = directory / "SHA256SUMS.txt"
    if output.is_symlink():
        raise ValueError("Checksum output must not be a symlink")
    fd, name = tempfile.mkstemp(prefix=".vox-checksum-", dir=directory)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(f"{digest}  {dmg.name}\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, output)
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--write-checksum", action="store_true")
    args = parser.parse_args()
    verify(args.dist, args.version)
    if args.write_checksum:
        checksum(args.dist, args.version)
    print("Cloud release assets verified; no legacy updater feed")
