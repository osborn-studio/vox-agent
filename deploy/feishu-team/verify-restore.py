#!/usr/bin/env python3
"""Verify a decrypted backup in isolation. Never starts services or writes config.

Feed a tar produced by GPG to --tar. Only copies validated regular SQLite members
into a private temporary directory, checks integrity and manifest counts, then
removes them. This is a storage restore check, not a session-revocation rehearsal.
"""

from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import tarfile
import tempfile

MAX_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 128


def verify(
    archive: Path,
    staging: Path,
    expected_databases: set[str] | None = None,
    *,
    allow_legacy: bool = False,
) -> dict:
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > MAX_BYTES:
        raise ValueError("Invalid archive")
    if staging.is_symlink() or not staging.is_dir() or staging.stat().st_mode & 0o077:
        raise ValueError("Staging directory must be private")
    with tempfile.TemporaryDirectory(dir=staging, prefix="restore-") as temp:
        with tarfile.open(archive, "r:") as bundle:
            seen = set()
            total = 0
            databases = 0
            database_names = set()
            manifest = None
            config_seen = False
            observed = {}
            for index, member in enumerate(bundle):
                if index >= MAX_MEMBERS:
                    raise ValueError("Too many archive members")
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or path.parts[0] != "snapshot"
                ):
                    raise ValueError("Unsafe member path")
                normalized = str(path)
                if normalized in seen:
                    raise ValueError("Duplicate member")
                seen.add(normalized)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError("Non-regular archive member")
                total += member.size
                if member.size < 0 or total > MAX_BYTES:
                    raise ValueError("Archive size limit")
                with closing(bundle.extractfile(member)) as source:
                    if normalized in ("snapshot/manifest.json", "snapshot/runtime.json"):
                        if member.size > 1024 * 1024:
                            raise ValueError("Oversized metadata")
                        metadata = source.read()
                        value = json.loads(metadata)
                        if not isinstance(value, dict):
                            raise ValueError("Invalid metadata")
                        if normalized.endswith("/manifest.json"):
                            manifest = value
                        else:
                            config_seen = True
                            observed["runtime.json"] = {
                                "size": len(metadata),
                                "sha256": hashlib.sha256(metadata).hexdigest(),
                            }
                        continue
                    if source.read(16) != b"SQLite format 3\x00":
                        raise ValueError("Unexpected backup member")
                    target = Path(temp) / f"{index}.sqlite"
                    with target.open("xb") as output:
                        output.write(b"SQLite format 3\x00")
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                    with closing(
                        sqlite3.connect(target.as_uri() + "?mode=ro&immutable=1", uri=True)
                    ) as database:
                        database.execute("PRAGMA trusted_schema=OFF")
                        if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                            raise ValueError("Restored database integrity failed")
                    with target.open("rb") as copied:
                        observed[str(path.relative_to("snapshot"))] = {
                            "size": target.stat().st_size,
                            "sha256": hashlib.file_digest(copied, "sha256").hexdigest(),
                        }
                    databases += 1
                    database_names.add(normalized)
            if expected_databases is not None and database_names != expected_databases:
                raise ValueError("Database inventory mismatch")
            if (
                not config_seen
                or not isinstance(manifest, dict)
                or type(manifest.get("databases")) is not int
                or databases == 0
                or databases != manifest["databases"]
            ):
                raise ValueError("Backup manifest mismatch")
            version = manifest.get("schemaVersion")
            if version is None and allow_legacy and expected_databases is not None:
                manifest_verified = False
            elif type(version) is int and version == 2:
                entries = manifest.get("files")
                if (
                    not isinstance(entries, dict)
                    or any(
                        not isinstance(v, dict)
                        or type(v.get("size")) is not int
                        or not isinstance(v.get("sha256"), str)
                        for v in entries.values()
                    )
                    or entries != observed
                ):
                    raise ValueError("Backup content manifest mismatch")
                manifest_verified = True
            else:
                raise ValueError(
                    "Unsupported manifest version; legacy requires explicit opt-in and inventory"
                )
    return {
        "databases": databases,
        "integrity": "ok",
        "production_modified": False,
        "manifest_verified": manifest_verified,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tar", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--expected-database", action="append")
    parser.add_argument("--allow-legacy-manifest", action="store_true")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                verify(
                    args.tar,
                    args.staging,
                    set(args.expected_database) if args.expected_database else None,
                    allow_legacy=args.allow_legacy_manifest,
                )
            )
        )
    except Exception:
        # Never expose employee text or configuration in exception messages.
        raise SystemExit("Isolated restore verification failed") from None
