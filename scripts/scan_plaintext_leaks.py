#!/usr/bin/env python3
"""Plaintext leak scanner for the Windows Context data paths (T44c).

Scans every file under the given roots (database, WAL/SHM, temp files, logs,
exports) for synthetic sensitive markers in UTF-8, UTF-16 LE/BE, hex and
base64 (all three alignments). Symlinks are not followed. The JSON report
names only relative file paths, marker indexes and encodings: never a marker
or any file content. Exit 1 when anything is found.

  # scan existing directories
  python scripts/scan_plaintext_leaks.py --root DIR --marker TEXT [--marker TEXT ...]

  # run the Rust Context flow with fresh random markers, then scan what it left
  python scripts/scan_plaintext_leaks.py --cargo-flow
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

CHUNK = 1 << 20
# What a zero-hit result does and does not show.
COVERAGE = (
    "raw file bytes: utf-8, utf-16-le/be, hex, unwrapped base64 at every alignment; "
    "not compressed, encrypted-by-others, line-wrapped base64 or content split across "
    "non-contiguous storage pages"
)
MIN_MARKER = 8
REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "apps/desktop-tauri/src-tauri/Cargo.toml"


def _base64_forms(data: bytes) -> list[bytes]:
    """Base64 substrings fully determined by `data`, for each alignment.

    If `data` starts at byte offset p (mod 3) of the encoded stream, its first
    (3 - p) % 3 bytes share a group with unknown bytes. Every complete group of
    `data` bytes after that encodes to fixed characters: the base64 of those
    bytes, with no padding.
    """
    forms = []
    for skip in range(3):
        groups = (len(data) - skip) // 3
        if groups >= 2:
            forms.append(base64.b64encode(data[skip : skip + 3 * groups]))
    return forms


def patterns(marker: str) -> list[tuple[str, bytes]]:
    raw = marker.encode("utf-8")
    out = [
        ("utf-8", raw),
        ("utf-16-le", marker.encode("utf-16-le")),
        ("utf-16-be", marker.encode("utf-16-be")),
        ("hex", raw.hex().encode()),
        ("hex", raw.hex().upper().encode()),
    ]
    out += [("base64", form) for form in _base64_forms(raw)]
    return out


def _files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            path = Path(dirpath, name)
            if not path.is_symlink() and path.is_file():
                yield path


def _file_hits(path: Path, compiled: list[tuple[int, str, bytes]]) -> set[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    overlap = max(len(p) for _, _, p in compiled) - 1
    tail = b""
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            window = tail + chunk
            for index, encoding, pattern in compiled:
                if (index, encoding) not in found and pattern in window:
                    found.add((index, encoding))
            tail = window[-overlap:] if overlap else b""
    return found


def scan(roots: list[Path], markers: list[str]) -> dict:
    if not markers:
        raise ValueError("at least one marker is required")
    for marker in markers:
        if len(marker) < MIN_MARKER:
            raise ValueError("markers must be at least 8 characters to avoid false hits")
    compiled = [(i, enc, pat) for i, m in enumerate(markers) for enc, pat in patterns(m)]
    hits, scanned = [], 0
    for root in roots:
        root = Path(root)
        for path in _files(root):
            scanned += 1
            for index, encoding in sorted(_file_hits(path, compiled)):
                relative = path.relative_to(root).as_posix()
                # The report itself must never carry a marker.
                if any(marker in relative for marker in markers):
                    relative = f"<redacted path {scanned}>"
                hits.append({"file": relative, "marker": index, "encoding": encoding})
    return {"scanned_files": scanned, "hits": hits, "coverage": COVERAGE}


def cargo_flow() -> int:
    """Run the Rust flow with random markers in a fresh directory and scan it."""
    with tempfile.TemporaryDirectory(prefix="vox-leak-") as work:
        work_path = Path(work)
        (work_path / "tmp").mkdir()
        token = "leak" + secrets.token_hex(12)
        markers = {
            "text": "leak-text-" + secrets.token_hex(8),
            "repository": f"leak{secrets.token_hex(4)}/repo{secrets.token_hex(4)}",
            "token": token,
        }
        env = dict(os.environ)
        env.update({
            "VOX_LEAK_SCAN_DIR": str(work_path / "data"),
            "VOX_LEAK_MARKERS": json.dumps(markers),
            "TMPDIR": str(work_path / "tmp"),
            "TMP": str(work_path / "tmp"),
            "TEMP": str(work_path / "tmp"),
        })
        result = subprocess.run(
            ["cargo", "test", "--locked", "--manifest-path", str(MANIFEST),
             "--test", "plaintext_leak_flow", "--", "--nocapture"],
            env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(json.dumps({"error": "cargo flow failed", "exit": result.returncode}))
            return 2
        report = scan([work_path / "data", work_path / "tmp"], list(markers.values()))
        report["markers"] = list(markers.keys())  # names only, never values
        print(json.dumps(report, indent=2))
        return 1 if report["hits"] else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", action="append", default=[], type=Path)
    parser.add_argument("--marker", action="append", default=[])
    parser.add_argument("--cargo-flow", action="store_true")
    args = parser.parse_args(argv)
    if args.cargo_flow:
        return cargo_flow()
    if not args.root:
        parser.error("--root is required")
    report = scan(args.root, args.marker)
    print(json.dumps(report, indent=2))
    return 1 if report["hits"] else 0


if __name__ == "__main__":
    sys.exit(main())
