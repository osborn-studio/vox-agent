"""Write a schema-1 model manifest from a locally downloaded archive.

Operator tool for T31. It never downloads: fetch the release archive or the
single files yourself, inspect them, then pin them so installs are verified
byte for byte. Files mode (`--files-dir`, `--file`, `--base-url`) pins single
files fetched from `<base-url><name>`; prefer a base URL pinned to a commit.

Usage:
  .venv/bin/python scripts/pin_model_manifest.py --archive bundle.tar.bz2 \\
      --url https://github.com/.../bundle.tar.bz2 --id sensevoice-int8 \\
      --provider sherpa-sensevoice --revision 2024-07-17 --license Apache-2.0 \\
      --strip-prefix bundle --out engine/model_manifests/sensevoice-int8.json

Every regular file under the prefix is pinned; the cache refuses archive
members that the manifest does not list.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.model_cache import (  # noqa: E402
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    ModelCacheError,
    ModelManifest,
    canonical_digest,
)


def _sha256(path: Path, limit: int | None = None) -> str:
    """SHA-256 of a file; with `limit`, refuse a file that reads past it."""
    digest, total = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20 if limit is None else min(1 << 20, limit + 1 - total)):
            total += len(chunk)
            if limit is not None and total > limit:
                raise ModelCacheError(f"file grew while pinning: {path.name}")
            digest.update(chunk)
    if limit is not None and total != limit:
        raise ModelCacheError(f"file changed while pinning: {path.name}")
    return digest.hexdigest()


def _archive_entries(archive: Path, prefix: str) -> list[dict]:
    entries, total = [], 0
    with tarfile.open(archive, "r:*") as tar:
        for member in tar:
            if member.isdir() and (member.name + "/").startswith(prefix):
                continue
            if not member.isreg():
                raise ModelCacheError(f"archive member is not a regular file: {member.name}")
            if not member.name.startswith(prefix):
                raise ModelCacheError(f"archive member is outside the prefix: {member.name}")
            # Limits are checked from the header, before any byte is read.
            total += member.size
            if len(entries) >= MAX_FILES or member.size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ModelCacheError("archive exceeds the manifest limits")
            source = tar.extractfile(member)
            if source is None:
                raise ModelCacheError(f"archive member cannot be read: {member.name}")
            digest, size = hashlib.sha256(), 0
            with source:
                while chunk := source.read(1 << 20):
                    digest.update(chunk)
                    size += len(chunk)
                    if size > member.size:
                        raise ModelCacheError(f"archive member is larger than its header: {member.name}")
            entries.append({"path": member.name[len(prefix):], "size": size, "sha256": digest.hexdigest()})
    return entries


def _file_entries(folder: Path, names: list[str]) -> list[dict]:
    """Size and SHA-256 of downloaded single files (regular files only)."""
    if not 0 < len(names) <= MAX_FILES:
        raise ModelCacheError("file count exceeds the manifest limits")
    sizes, total = {}, 0
    # Limits are checked from the file system before any byte is read.
    for name in names:
        if "/" in name or "\\" in name or name in (".", ".."):
            raise ModelCacheError(f"file name must be a plain name: {name}")
        info = os.lstat(folder / name)
        if not stat.S_ISREG(info.st_mode):
            raise ModelCacheError(f"not a regular file: {name}")
        total += info.st_size
        if info.st_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ModelCacheError("files exceed the manifest limits")
        sizes[name] = info.st_size
    entries = []
    for name in names:
        digest = _sha256(folder / name, limit=sizes[name])
        entries.append({"path": name, "size": sizes[name], "sha256": digest})
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--archive", type=Path, help="pin every regular file of a downloaded archive")
    mode.add_argument("--files-dir", type=Path, help="pin downloaded single files from this folder")
    parser.add_argument("--url", help="archive URL (with --archive)")
    parser.add_argument("--file", action="append", default=[], help="file name in --files-dir (repeatable)")
    parser.add_argument("--base-url", help="URL prefix the files are fetched from (with --files-dir)")
    parser.add_argument("--id", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--architecture", default="any")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--license", required=True)
    parser.add_argument("--strip-prefix", default="")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        if args.archive is not None:
            if not args.url or args.file or args.base_url:
                raise ModelCacheError("--archive needs --url and takes no --file/--base-url")
            prefix = args.strip_prefix.strip("/")
            prefix = f"{prefix}/" if prefix else ""
            files = _archive_entries(args.archive, prefix)
            source = {
                "kind": "archive",
                "url": args.url,
                "size": args.archive.stat().st_size,
                "sha256": _sha256(args.archive),
                "stripPrefix": prefix,
            }
        else:
            if not args.file or not args.base_url or args.url or args.strip_prefix:
                raise ModelCacheError("--files-dir needs --file and --base-url")
            files = _file_entries(args.files_dir, args.file)
            source = {"kind": "files", "baseUrl": args.base_url}
        manifest = {
            "schema": 1,
            "id": args.id,
            "provider": args.provider,
            "architecture": args.architecture,
            "revision": args.revision,
            "license": args.license,
            "source": source,
            "files": sorted(files, key=lambda e: e["path"].encode()),
            "digest": canonical_digest(files),
        }
        text = json.dumps(manifest, indent=2, ensure_ascii=True) + "\n"
        ModelManifest.parse(text)
    except (ModelCacheError, tarfile.TarError, OSError, EOFError) as exc:
        print(f"cannot pin manifest: {exc}", file=sys.stderr)
        return 1
    args.out.write_text(text, encoding="utf-8")
    print(f"pinned {len(files)} files ({manifest['digest']}) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
