#!/usr/bin/env python3
"""Create-only opaque Inputs/Drafts export; no key lookup, decryption or restore."""
import argparse
import base64
import secrets
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

if __package__:
    from .inspect_context_private_storage import InventoryError, SNAPSHOT, _chain
else:
    from inspect_context_private_storage import InventoryError, SNAPSHOT, _chain


class ExportError(Exception):
    pass


def _private_file(info, maximum):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and info.st_nlink == 1 and not info.st_mode & 0o077
            and 0 < info.st_size <= maximum)


def export_snapshots(root, boundary, output, *, store):
    if store not in ("Inputs", "Drafts"):
        raise ExportError("UNSUPPORTED_STORE")
    root, boundary, output = (Path(os.path.abspath(p)) for p in (root, boundary, output))
    source = root / store
    try:
        _chain(source, boundary)
        _chain(output.parent, boundary)
    except InventoryError:
        raise ExportError("UNSAFE_BOUNDARY") from None
    if output == root or root in output.parents:
        raise ExportError("OUTPUT_INSIDE_SOURCE")
    directory = lock = parent = None
    staging = None
    cleanup_warning = False
    try:
        directory = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ExportError("UNSAFE_SOURCE")
        lock = os.open(".vault.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        li = os.fstat(lock)
        if not stat.S_ISREG(li.st_mode) or li.st_uid != os.geteuid() or li.st_nlink != 1 or li.st_mode & 0o077:
            raise ExportError("UNSAFE_LOCK")
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExportError("STORE_BUSY") from None
        names = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name == ".vault.lock":
                    continue
                if len(names) >= 64 or not SNAPSHOT.fullmatch(entry.name):
                    raise ExportError("UNRECOGNIZED_OR_EXCESS_ENTRIES")
                names.append(entry.name)
        # Validate every entry before reserving an output directory.
        maximum = 64_000 if store == "Inputs" else 2_000_000
        total = 0
        for name in names:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not _private_file(info, maximum):
                raise ExportError("UNSAFE_FILE")
            total += info.st_size
        if total > (4_000_000 if store == "Inputs" else 64_000_000):
            raise ExportError("CAPACITY")
        if not names:
            raise ExportError("EMPTY_STORE")
        records = []
        for name in sorted(names):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
            try:
                before = os.fstat(fd)
                if not _private_file(before, maximum):
                    raise ExportError("UNSAFE_FILE")
                with os.fdopen(os.dup(fd), "rb") as handle:
                    data = handle.read(maximum + 1)
                after = os.fstat(fd)
                fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
                if any(getattr(before, k) != getattr(after, k) for k in fields) or len(data) != before.st_size:
                    raise ExportError("SOURCE_CHANGED")
            finally:
                os.close(fd)
            records.append({"name": name, "bytes": len(data), "source_sha256": hashlib.sha256(data).hexdigest(),
                            "opaque_base64": base64.b64encode(data).decode("ascii")})
        # Release the cooperating-writer lock before destination I/O.
        os.close(lock)
        lock = None
        parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        # Physical ancestor identity catches case/Unicode aliases on APFS.
        parent_info = os.fstat(parent)
        if parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o077:
            raise ExportError("UNSAFE_OUTPUT_PARENT")
        boundary_info = os.stat(boundary, follow_symlinks=False)
        found_boundary = False
        root_info = os.stat(root, follow_symlinks=False)
        ancestor = os.dup(parent)
        try:
            while True:
                current = os.fstat(ancestor)
                if (current.st_dev, current.st_ino) == (boundary_info.st_dev, boundary_info.st_ino):
                    found_boundary = True
                if (current.st_dev, current.st_ino) == (root_info.st_dev, root_info.st_ino):
                    raise ExportError("OUTPUT_INSIDE_SOURCE")
                upper = os.open("..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ancestor)
                up = os.fstat(upper)
                if (up.st_dev, up.st_ino) == (current.st_dev, current.st_ino):
                    os.close(upper)
                    break
                os.close(ancestor)
                ancestor = upper
        finally:
            os.close(ancestor)
        if not found_boundary:
            raise ExportError("OUTPUT_OUTSIDE_BOUNDARY")
        receipt = {"version": 2, "store": store, "scope": "opaque-bundle-no-restore",
                   "files": records, "keychain": "NOT_RUN", "authentication": "NOT_RUN",
                   "recovery": "NOT_RUN", "durability": "NOT_ESTABLISHED"}
        staging = ".vox-export-" + secrets.token_hex(16)
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=parent)
        try:
            payload = json.dumps(receipt, sort_keys=True).encode("utf-8")
            offset = 0
            while offset < len(payload):
                written = os.write(fd, payload[offset:])
                if written <= 0:
                    raise ExportError("INCOMPLETE_WRITE")
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)
        # Atomic create-only publication of one complete regular file.
        os.link(staging, output.name, src_dir_fd=parent, dst_dir_fd=parent)
        try:
            os.unlink(staging, dir_fd=parent)
        except OSError:
            cleanup_warning = True
        staging = None
        return {"status": "COPIED_NOT_RECOVERY_VERIFIED", "files": len(records), "bytes": sum(r["bytes"] for r in records), "private_staging_cleanup_warning": cleanup_warning}
    except FileExistsError:
        raise ExportError("EXISTING_OUTPUT") from None
    except (OSError, NotImplementedError):
        raise ExportError("IO_OR_UNSUPPORTED_PLATFORM") from None
    finally:
        if staging is not None and parent is not None:
            try:
                os.unlink(staging, dir_fd=parent)
            except OSError:
                pass  # Preserve original refusal; incomplete private staging is never published.
        if parent is not None:
            os.close(parent)
        if lock is not None:
            os.close(lock)
        if directory is not None:
            os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "boundary", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--store", choices=("Inputs", "Drafts"), required=True)
    args = parser.parse_args()
    try:
        value = export_snapshots(args.root, args.boundary, args.output, store=args.store)
    except ExportError as error:
        print(json.dumps({"status": "REFUSED_OR_INCOMPLETE", "code": str(error), "originals": "RETAINED"}))
        return 2
    print(json.dumps(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
