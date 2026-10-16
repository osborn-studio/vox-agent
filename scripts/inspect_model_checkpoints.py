"""Bounded, metadata-only diagnostics for Vox complete-file checkpoint storage.

Never acquire leases, read payloads, create files, repair permissions, or remove data.
This report is not proof that any entry is idle, verified, or safe to delete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat

CONTAINER = "VoxAgentModelDownloadCheckpoints"
MAX_ENTRIES = 128
JOB = re.compile(r"^\.qwen-checkpoint-[a-f0-9]{64}$")


class InspectionError(Exception):
    """Stable, content-free diagnostic error."""


def _open_directory(path: str) -> int:
    if not os.path.isabs(path) or ".." in path.split(os.sep):
        raise InspectionError("absolute_nontraversing_path_required")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(os.sep, flags)
    try:
        for part in path.split(os.sep):
            if part and part != ".":
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private_directory(fd: int) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise InspectionError("unsafe_directory")
    return info


def _entries(fd: int) -> list[tuple[str, os.stat_result]]:
    before = _private_directory(fd)
    rows = []
    with os.scandir(fd) as scan:
        for entry in scan:
            if len(rows) >= MAX_ENTRIES:
                raise InspectionError("entry_limit_exceeded")
            info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
            if (
                not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
            ):
                raise InspectionError("unsafe_entry")
            rows.append((entry.name, info))
    after = os.fstat(fd)
    if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
        raise InspectionError("directory_changed_during_scan")
    return rows


def inspect(parent: str) -> dict:
    parent_fd = _open_directory(parent)
    try:
        _private_directory(parent_fd)
        try:
            fd = os.open(
                CONTAINER, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return {"schema": 1, "present": False, "readOnly": True, "entries": []}
    finally:
        os.close(parent_fd)
    try:
        rows = _entries(fd)
        entries = []
        counts = {"job": 0, "retained": 0, "quarantine": 0, "lock": 0, "unknown": 0}
        for name, info in sorted(rows):
            kind = (
                "job" if JOB.fullmatch(name)
                else "retained" if name.startswith(".retained-job-")
                else "quarantine" if name.startswith(".quarantine-")
                else "lock" if name.endswith(".lock")
                else "unknown"
            )
            counts[kind] += 1
            # Opaque IDs let operators compare reports without printing arbitrary filenames.
            row = {"id": hashlib.sha256(os.fsencode(name)).hexdigest(), "kind": kind}
            if stat.S_ISDIR(info.st_mode):
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=fd,
                )
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise InspectionError("entry_replaced_during_scan")
                    children = _entries(child)
                    if any(stat.S_ISDIR(s.st_mode) for _, s in children):
                        raise InspectionError("nested_directory_unconfirmed")
                    row.update(files=len(children), logicalBytes=sum(s.st_size for _, s in children))
                finally:
                    os.close(child)
            else:
                row.update(files=1, logicalBytes=info.st_size)
            entries.append(row)
        return {
            "schema": 1, "present": True, "readOnly": True,
            "snapshot": "best_effort_metadata_only_no_lease",
            "deletionAuthorized": False, "counts": counts, "entries": entries,
            "capacityPressure": {
                "newMirrorJob": counts["job"] >= 4,
                "quarantine": counts["quarantine"] >= 4,
                "retainedJob": counts["retained"] >= 4,
                "newJobAndLock": len(rows) + 2 > MAX_ENTRIES,
            },
        }
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True, help="Private model cache parent directory")
    args = parser.parse_args()
    try:
        result = inspect(args.parent)
    except (OSError, InspectionError) as error:
        # OSError messages contain paths: expose only the numeric errno.
        code = str(error) if isinstance(error, InspectionError) else f"filesystem_errno_{error.errno}"
        print(json.dumps({"schema": 1, "status": "unconfirmed", "error": code}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
