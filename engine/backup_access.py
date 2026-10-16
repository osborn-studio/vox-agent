"""Local administrator tool for explicit backup target access. Preview by default.

No Feishu API calls. Applying config requires the service to be stopped; activation
requires a subsequent restart. Never prints configuration or validation details.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile
import time
import uuid

from engine.backup_runtime import RuntimeSettings
from engine.org_schema import ORG_DATABASE_NAME


class AccessError(Exception):
    pass


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_config(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        meta = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(meta.st_mode)
            or meta.st_nlink != 1
            or meta.st_mode & 0o027
            or os.geteuid() not in (0, meta.st_uid)
        ):
            raise AccessError("UNSAFE_CONFIG_PERMISSIONS")
        data = source.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise AccessError("CONFIG_TOO_LARGE")
    try:
        raw = json.loads(data)
        settings = RuntimeSettings.model_validate(raw)
    except Exception:
        raise AccessError("INVALID_CONFIG") from None
    return data, raw, settings, meta


def lookup_user(settings, user_id):
    path = Path(settings.data_dir) / "identity" / ORG_DATABASE_NAME
    if path.is_symlink() or not path.is_file():
        raise AccessError("IDENTITY_STORE_UNAVAILABLE")
    try:
        with sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT org_id, status FROM user WHERE id = ?", (user_id,)).fetchone()
            return row
    except sqlite3.Error:
        raise AccessError("IDENTITY_STORE_UNAVAILABLE") from None


def plan(raw, settings, action, org_id, target_id, user_id=None):
    for value in (org_id, target_id, user_id) if user_id else (org_id, target_id):
        if not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,160}", value):
            raise AccessError("INVALID_IDENTIFIER")
    if action == "disable-target" and user_id is not None:
        raise AccessError("DISABLE_TARGET_DOES_NOT_ACCEPT_USER")
    matches = [t for t in settings.targets if t.org_id == org_id and t.id == target_id]
    if len(matches) != 1:
        raise AccessError("TARGET_NOT_UNIQUE_OR_MISSING")
    if settings.feishu_login and target_id in settings.feishu_login.backup_target_ids:
        raise AccessError("TENANT_WIDE_GRANT_REQUIRES_MANUAL_REVIEW")
    target = matches[0]
    updated = copy.deepcopy(raw)
    item = next(t for t in updated["targets"] if t["id"] == target_id and t["org_id"] == org_id)
    users = list(item["allowed_users"])
    if action == "grant":
        if lookup_user(settings, user_id) != (org_id, "active"):
            raise AccessError("MEMBER_NOT_ACTIVE_IN_TARGET_ORG")
        if user_id not in users:
            users.append(user_id)
        item["allowed_users"] = users
    elif action == "revoke":
        item["allowed_users"] = [value for value in users if value != user_id]
        if not item["allowed_users"]:
            raise AccessError("LAST_MEMBER_USE_DISABLE_TARGET")
    elif action == "disable-target":
        updated["targets"].remove(item)
    else:
        raise AccessError("INVALID_ACTION")
    RuntimeSettings.model_validate(updated)
    changed = updated != raw
    return updated, {
        "action": action,
        "org_id": org_id,
        "target_id": target_id,
        "user_id": user_id,
        "changed": changed,
        "before_member_count": len(target.allowed_users),
        "after_member_count": 0 if action == "disable-target" else len(item["allowed_users"]),
        "activation": "restart_required" if changed else "unchanged",
    }


def require_stopped():
    result = subprocess.run(
        [
            "systemctl",
            "show",
            "vox-team.service",
            "--property=LoadState,ActiveState,MainPID",
            "--no-pager",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if (
        values.get("LoadState") != "loaded"
        or values.get("ActiveState") not in ("inactive", "failed")
        or values.get("MainPID") != "0"
    ):
        raise AccessError("STOP_VOX_TEAM_BEFORE_APPLY")


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def execute(path, action, org_id, target_id, user_id=None, apply=False):
    path = Path(path).absolute()
    # The protected config directory is the administrator trust boundary.
    parent = path.parent.stat()
    if path.parent.is_symlink() or parent.st_mode & 0o022 or os.geteuid() not in (0, parent.st_uid):
        raise AccessError("UNSAFE_CONFIG_DIRECTORY")
    lock = None
    try:
        if apply:
            lock = os.open(
                path.with_name(path.name + ".access.lock"),
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
            meta = os.fstat(lock)
            if (
                not stat.S_ISREG(meta.st_mode)
                or meta.st_nlink != 1
                or meta.st_uid != os.geteuid()
                or meta.st_mode & 0o077
            ):
                raise AccessError("UNSAFE_LOCK")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require_stopped()
        before, raw, settings, metadata = read_config(path)
        updated, report = plan(raw, settings, action, org_id, target_id, user_id)
        report["applied"] = False
        if not apply or not report["changed"]:
            return report
        after = encode(updated)
        changes = path.with_name(path.name + ".access-changes")
        changes.mkdir(mode=0o700, exist_ok=True)
        mode = changes.stat()
        if changes.is_symlink() or mode.st_uid != os.geteuid() or mode.st_mode & 0o077:
            raise AccessError("UNSAFE_CHANGE_DIRECTORY")
        change_id = uuid.uuid4().hex
        change = changes / change_id
        change.mkdir(mode=0o700)
        private_write(change / "before.json", before)
        private_write(change / "after.json", after)
        # Durable intent precedes replacement; recovery compares actual config hash.
        private_write(
            change / "intent.json",
            encode(
                {
                    **report,
                    "change_id": change_id,
                    "admin_uid": os.geteuid(),
                    "created_at": int(time.time()),
                    "before_sha256": digest(before),
                    "after_sha256": digest(after),
                }
            ),
        )
        fsync_dir(change)
        fsync_dir(changes)
        fsync_dir(path.parent)
        fd, temp = tempfile.mkstemp(prefix=".access-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                os.fchmod(out.fileno(), stat.S_IMODE(metadata.st_mode))
                os.fchown(out.fileno(), metadata.st_uid, metadata.st_gid)
                out.write(after)
                out.flush()
                os.fsync(out.fileno())
            if read_config(path)[0] != before:
                raise AccessError("CONFIG_CHANGED_RETRY_PREVIEW")
            require_stopped()
            os.replace(temp, path)
            fsync_dir(path.parent)
        finally:
            Path(temp).unlink(missing_ok=True)
        return {**report, "applied": True, "change_id": change_id}
    finally:
        if lock is not None:
            os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--org")
    parser.add_argument("--target")
    parser.add_argument("--user")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("action", choices=["list", "grant", "revoke", "disable-target"])
    args = parser.parse_args()
    if args.action == "list":
        if args.apply or args.org or args.target or args.user:
            parser.error("list accepts only --config")
    elif not args.org or not args.target:
        parser.error("--org and --target are required")
    if args.action in ("grant", "revoke") and not args.user:
        parser.error("--user is required")
    try:
        if args.action == "list":
            _, _, settings, _ = read_config(args.config)
            wide = settings.feishu_login.backup_target_ids if settings.feishu_login else []
            print(
                json.dumps(
                    {
                        "targets": [
                            {
                                "org_id": t.org_id,
                                "target_id": t.id,
                                "allowed_users": t.allowed_users,
                                "tenant_wide_grant": t.id in wide,
                            }
                            for t in settings.targets
                        ],
                        "source": "on_disk_config_not_live_state",
                    }
                )
            )
            return 0
        print(
            json.dumps(
                execute(args.config, args.action, args.org, args.target, args.user, args.apply)
            )
        )
    except AccessError as error:
        print(
            json.dumps(
                {
                    "error": str(error),
                    "applied": "inspect_config_and_change_intent" if args.apply else False,
                }
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "error": "ACCESS_OPERATION_FAILED",
                    "applied": "inspect_config_and_change_intent" if args.apply else False,
                }
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
