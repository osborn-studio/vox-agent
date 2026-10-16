#!/usr/bin/env python3
"""Inspect a checksum-pinned legacy DMG without executing its bundled code.

Output is provenance evidence, NOT a runtime-trusted model manifest. Upstream
revision/license verification and inclusion in a signed App are separate gates.
"""

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile


def file_hash(path):
    with path.open("rb") as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def inventory(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected a regular model directory")
    files = []
    names = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", relative) or any(
            part in ("", ".", "..") or part.endswith(".") for part in relative.split("/")
        ):
            raise ValueError("Unsafe model path")
        if relative.lower() in names:
            raise ValueError("Case-colliding model path")
        names.add(relative.lower())
        if path.is_symlink():
            raise ValueError("Model symlinks are not accepted")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Non-regular model file")
        files.append({"path": relative, "size": path.stat().st_size, "sha256": file_hash(path)})
    if not files:
        raise ValueError("Empty model tree")
    return sorted(files, key=lambda item: item["path"].encode("utf-8"))


def canonical_digest(files):
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def inspect(dmg, expected_sha):
    if not re.fullmatch("[0-9a-f]{64}", expected_sha) or dmg.is_symlink() or not dmg.is_file():
        raise ValueError("Invalid source or checksum")
    temp = Path(tempfile.mkdtemp(prefix="vox-model-inspect-"))
    mount = temp / "media"
    mount.mkdir()
    attached = False
    try:
        snapshot = temp / "source.dmg"
        shutil.copyfile(dmg, snapshot)
        if file_hash(snapshot) != expected_sha:
            raise ValueError("Legacy DMG checksum mismatch")
        # Mark before attach: an interrupted attach may still have mounted media.
        attached = True
        subprocess.run(
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-noautoopen",
                "-mountpoint",
                str(mount),
                str(snapshot),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        app = mount / "Vox Agent.app"
        info_path = contained_path(mount, app / "Contents/Info.plist")
        with info_path.open("rb") as source:
            info = plistlib.load(source)
        version = info.get("CFBundleShortVersionString")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("Invalid bundle version")
        files = inventory(contained_path(mount, app / "Contents/Resources/Models/Qwen3-ASR"))
        return {
            "evidence_schema": 1,
            "runtime_trusted": False,
            "source_dmg_sha256": expected_sha,
            "source_version": version,
            "model_id": "qwen3-asr-0.6b",
            "manifest_digest": canonical_digest(files),
            "files": files,
            "remaining_gates": [
                "upstream revision and file/license verification",
                "signed App embedding",
            ],
        }
    finally:
        if attached:
            detached = False
            for extra in ([], ["-force"]):
                result = subprocess.run(
                    ["hdiutil", "detach", *extra, str(mount)],
                    stdout=subprocess.DEVNULL,
                )
                if result.returncode == 0:
                    detached = True
                    break
            if not detached:
                # Never recursively remove a potentially mounted filesystem.
                raise RuntimeError(f"Detach failed; inspection files retained at {temp}")
        shutil.rmtree(temp)


def contained_path(root, target):
    relative = target.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink in bundle path")
    target.resolve(strict=True).relative_to(root.resolve(strict=True))
    return target


def write_evidence(output, result):
    data = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, name = tempfile.mkstemp(prefix=".vox-evidence-", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.link(temporary, output)  # Atomic, fails if any destination already exists.
    finally:
        temporary.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dmg", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.dmg, args.expected_sha256)
    write_evidence(args.output, result)
