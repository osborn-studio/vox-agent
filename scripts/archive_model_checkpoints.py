"""Preview then archive selected preserved checkpoints; never delete payloads.

Execution requires an explicit private plan and all cooperative download leases.
Archives are siblings of the checkpoint container, so this frees entry quota,
not disk space. No real-user invocation is part of automated verification.
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from contextlib import ExitStack

try:
    from .inspect_model_checkpoints import CONTAINER, InspectionError, _entries, _open_directory
    from .inspect_model_checkpoints import _private_directory
except ImportError:
    from inspect_model_checkpoints import CONTAINER, InspectionError, _entries, _open_directory
    from inspect_model_checkpoints import _private_directory

FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
UUID_PATTERN = r"[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}"
PRESERVED = re.compile(r"^\.(quarantine|retained-job)-" + UUID_PATTERN + r"$")
LOCK = re.compile(r"^\.qwen-checkpoint-[a-f0-9]{64}\.lock$")


class ArchiveError(InspectionError):
    def __init__(self, code, recovery=None):
        super().__init__(code)
        self.recovery = recovery


def identity(info):
    return [info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def opaque(name):
    return hashlib.sha256(os.fsencode(name)).hexdigest()


def bounded_rows(fd):
    # Independent directory cursor; repeated preflight scans must not share offsets.
    scan = os.open(".", FLAGS, dir_fd=fd)
    try:
        return _entries(scan)
    finally:
        os.close(scan)


def snapshot(fd, selected):
    rows = dict(bounded_rows(fd))
    by_id = {opaque(name): name for name in rows}
    targets = []
    for entry_id in selected:
        name = by_id.get(entry_id)
        match = PRESERVED.fullmatch(name or "")
        if not match:
            raise ArchiveError("selected_entry_not_preserved")
        info = rows[name]
        directory = match.group(1) == "retained-job"
        if directory != stat.S_ISDIR(info.st_mode):
            raise ArchiveError("preserved_type_mismatch")
        children = {}
        if directory:
            child = os.open(name, FLAGS, dir_fd=fd)
            try:
                if identity(os.fstat(child)) != identity(info):
                    raise ArchiveError("selected_entry_changed")
                for child_name, child_info in bounded_rows(child):
                    if not stat.S_ISREG(child_info.st_mode):
                        raise ArchiveError("nested_directory_unconfirmed")
                    children[opaque(child_name)] = identity(child_info)
            finally:
                os.close(child)
        targets.append({"id": entry_id, "identity": identity(info), "children": children})
    return {"entries": {opaque(n): identity(s) for n, s in rows.items()}, "targets": targets}, rows


def validate_selected(selected):
    if (not isinstance(selected, list) or not 1 <= len(selected) <= 8
            or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v)
                   for v in selected)
            or len(set(selected)) != len(selected)):
        raise ArchiveError("invalid_selection")


def preview(parent, selected):
    validate_selected(selected)
    parent_fd = _open_directory(parent)
    try:
        _private_directory(parent_fd)
        fd = os.open(CONTAINER, FLAGS, dir_fd=parent_fd)
        try:
            _private_directory(fd)
            state, _ = snapshot(fd, selected)
            return {"schema": 1, "operation": "preserve_archive_not_delete",
                    "container": identity(os.fstat(fd)), "selected": selected, "state": state}
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def exclusive_existing_lock(fd, name, stack):
    lock = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    stack.callback(os.close, lock)
    info = os.fstat(lock)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or info.st_mode & 0o077):
        raise ArchiveError("unsafe_lock")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ArchiveError("download_busy") from None


def rename_exclusive(source_fd, name, destination_fd):
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        function, flag = libc.renameatx_np, 4  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        function, flag = libc.renameat2, 1  # RENAME_NOREPLACE
    else:
        raise ArchiveError("exclusive_rename_unsupported")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                         ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(source_fd, os.fsencode(name), destination_fd, os.fsencode(name), flag) != 0:
        raise OSError(ctypes.get_errno(), "exclusive_rename_failed")


def archive(parent, plan):
    if (not isinstance(plan, dict) or set(plan) != {"schema", "operation", "container",
                                                 "selected", "state"}
            or type(plan["schema"]) is not int or plan["schema"] != 1
            or plan["operation"] != "preserve_archive_not_delete"):
        raise ArchiveError("invalid_plan")
    validate_selected(plan["selected"])
    recovery = None
    try:
        with ExitStack() as stack:
            parent_fd = _open_directory(parent)
            stack.callback(os.close, parent_fd)
            _private_directory(parent_fd)
            fd = os.open(CONTAINER, FLAGS, dir_fd=parent_fd)
            stack.callback(os.close, fd)
            _private_directory(fd)
            exclusive_existing_lock(fd, ".admission.lock", stack)
            rows = dict(bounded_rows(fd))
            if any(n.endswith(".lock") and n != ".admission.lock" and not LOCK.fullmatch(n)
                   for n in rows):
                raise ArchiveError("unknown_lock_provenance")
            # Runtime never unlinks job locks. Acquiring ALL historical job leases
            # also covers a retained directory whose original job name was renamed.
            locks = [n for n in rows if LOCK.fullmatch(n)]
            if not locks:
                raise ArchiveError("job_lease_provenance_unconfirmed")
            for name in sorted(locks):
                exclusive_existing_lock(fd, name, stack)
            actual, rows = snapshot(fd, plan["selected"])
            if identity(os.fstat(fd)) != plan["container"] or actual != plan["state"]:
                raise ArchiveError("preview_stale")
            archive_name = "VoxAgentModelRecovery-" + str(uuid.uuid4())
            os.mkdir(archive_name, 0o700, dir_fd=parent_fd)
            recovery = {"archive": archive_name, "moved": [], "status": "unconfirmed"}
            destination = os.open(archive_name, FLAGS, dir_fd=parent_fd)
            stack.callback(os.close, destination)
            _private_directory(destination)
            receipt_fd = os.open("receipt.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=destination)
            stack.callback(os.close, receipt_fd)
            # Durable intent precedes renames; a crash must leave enough information
            # to inspect the isolated archive without guessing what was selected.
            intent = json.dumps({"plan": plan, "selectedNames": {
                opaque(n): n for n in rows if opaque(n) in plan["selected"]
            }}, sort_keys=True).encode()
            with os.fdopen(os.dup(receipt_fd), "wb") as receipt:
                receipt.write(intent)
                receipt.flush()
                os.fsync(receipt.fileno())
            os.fsync(destination)
            os.fsync(parent_fd)
            for entry_id in plan["selected"]:
                name = next(n for n in rows if opaque(n) == entry_id)
                if identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != identity(rows[name]):
                    raise ArchiveError("selected_entry_changed")
                rename_exclusive(fd, name, destination)
                recovery["moved"].append(entry_id)
            os.fsync(destination)
            os.fsync(fd)
            recovery["status"] = "archived_not_deleted"
            return recovery
    except (OSError, InspectionError) as error:
        code = str(error) if isinstance(error, InspectionError) else f"filesystem_errno_{error.errno}"
        raise ArchiveError(code, recovery) from None


def read_plan(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 262144):
            raise ArchiveError("unsafe_plan_file")
        with os.fdopen(os.dup(fd), "rb") as source:
            data = source.read(262145)
        if len(data) > 262144 or identity(info) != identity(os.fstat(fd)):
            raise ArchiveError("plan_changed")
        # Bound nesting independently of Python/pytest's configurable recursion limit.
        depth = 0
        quoted = escaped = False
        for byte in data:
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (91, 123):
                depth += 1
                if depth > 32:
                    raise ArchiveError("plan_nesting_invalid")
            elif byte in (93, 125):
                depth -= 1
        try:
            return json.loads(data)
        except RecursionError:
            raise ArchiveError("plan_nesting_invalid") from None
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview-id", action="append")
    mode.add_argument("--apply-plan", help="Explicitly execute a previously reviewed private plan")
    args = parser.parse_args()
    try:
        result = (preview(args.parent, args.preview_id) if args.preview_id
                  else archive(args.parent, read_plan(args.apply_plan)))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, InspectionError) as error:
        code = (str(error) if isinstance(error, InspectionError)
                else f"filesystem_errno_{error.errno}" if isinstance(error, OSError)
                else "invalid_json_plan")
        print(json.dumps({"status": "unconfirmed", "error": code,
                          "recovery": getattr(error, "recovery", None)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
