#!/usr/bin/env python3
"""Read-only local team-service diagnosis. No message bodies, credentials or Feishu calls."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import time
import urllib.request

UNITS = ("vox-team.service", "vox-team-backup.service", "vox-team-cos.service")
TIMERS = ("vox-team-backup.timer", "vox-team-cos.timer")
BACKUP_MAX_AGE = 26 * 3600


def unknown(name):
    return {"name": name, "status": "unknown"}


def check_health(opener=urllib.request.urlopen):
    try:
        with opener("https://api.osbornvox.com/healthz", timeout=10) as response:
            raw = response.read(8193)
            if len(raw) > 8192:
                raise ValueError("Oversized health response")
            data = json.loads(raw)
        healthy = data.get("status") == "ok" and data.get("enabled") is True
        return {
            "name": "https_delivery",
            "status": "ok" if healthy else "attention",
            "evidence": "public_endpoint",
        }
    except Exception:
        return unknown("https_delivery")


def unit_properties(runner=subprocess.run):
    result = runner(
        [
            "systemctl",
            "show",
            *UNITS,
            *TIMERS,
            "--property=Id,LoadState,ActiveState,MainPID,Result,ExecMainStatus,ExecMainExitTimestamp,NextElapseUSecRealtime",
            "--no-pager",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
    )
    units = {}
    for block in result.stdout.strip().split("\n\n"):
        values = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if values.get("Id") in UNITS + TIMERS:
            units[values["Id"]] = values
    return units


def check_unit(name, values, now):
    if values.get("LoadState") != "loaded":
        return unknown(name)
    if name.endswith("_timer"):
        try:
            next_run = datetime.strptime(
                values["NextElapseUSecRealtime"], "%a %Y-%m-%d %H:%M:%S UTC"
            ).replace(tzinfo=timezone.utc)
            good = values.get("ActiveState") == "active" and next_run.timestamp() >= now - 60
            return {
                "name": name,
                "status": "ok" if good else "attention",
                "next_run_utc": next_run.isoformat(),
            }
        except (KeyError, ValueError):
            return (
                {"name": name, "status": "attention"}
                if values.get("ActiveState") != "active"
                else unknown(name)
            )
    if name == "service":
        good = (
            values.get("ActiveState") == "active"
            and values.get("MainPID", "0").isdigit()
            and int(values.get("MainPID", "0")) > 0
        )
        return {"name": name, "status": "ok" if good else "attention"}
    try:
        stamp = (
            datetime.strptime(values["ExecMainExitTimestamp"], "%a %Y-%m-%d %H:%M:%S UTC")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        age = now - stamp
        if age < -60:
            return unknown(name)
        good = (
            values.get("Result") == "success"
            and values.get("ExecMainStatus") == "0"
            and values.get("ActiveState") == "inactive"
            and age <= BACKUP_MAX_AGE
        )
        return {
            "name": name,
            "status": "ok" if good else "attention",
            "age_seconds": max(0, int(age)),
            "evidence": "systemd_last_exit_only",
        }
    except (KeyError, ValueError, OverflowError):
        return unknown(name)


def check_disk(path="/", disk_usage=shutil.disk_usage):
    try:
        total, _, free = disk_usage(path)
        if total <= 0 or not 0 <= free <= total:
            return unknown("disk")
        return {
            "name": "disk",
            "status": "ok" if free >= 10 * 1024**3 and free / total >= 0.20 else "attention",
            "free_bytes": free,
            "free_percent": round(100 * free / total, 1),
        }
    except OSError:
        return unknown("disk")


def check_snapshot(directory, now):
    try:
        if directory.is_symlink() or not directory.is_dir():
            return unknown("local_snapshot")
        snapshots = []
        for path in directory.iterdir():
            if not re.fullmatch(r"vox-\d{8}T\d{6}Z\.tar\.gpg", path.name):
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size < 16:
                return unknown("local_snapshot")
            stamp = (
                datetime.strptime(path.name, "vox-%Y%m%dT%H%M%SZ.tar.gpg")
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
            snapshots.append((stamp, info.st_size))
        if not snapshots:
            return {"name": "local_snapshot", "status": "attention", "count": 0}
        stamp, size = max(snapshots)
        age = now - stamp
        if age < -60:
            return unknown("local_snapshot")
        return {
            "name": "local_snapshot",
            "status": "ok" if age <= BACKUP_MAX_AGE else "attention",
            "age_seconds": max(0, int(age)),
            "size_bytes": size,
            "count": len(snapshots),
            "evidence": "file_presence_not_restore_verification",
        }
    except (OSError, ValueError, OverflowError):
        return unknown("local_snapshot")


def queue_path(config):
    fd = os.open(config, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Invalid config")
        data = source.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Config too large")
    root = json.loads(data)["data_dir"]
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise ValueError("Invalid data directory")
    return Path(root) / "input-backup.sqlite"


def check_queue(path, now):
    try:
        if path.is_symlink() or not path.is_file():
            return unknown("queue")
        deadline = time.monotonic() + 2
        with closing(
            sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            statuses = ("pending", "sending", "delivered", "unknown", "blocked", "rejected")
            counts = {
                s: db.execute("SELECT COUNT(*) FROM backup_outbox WHERE status=?", (s,)).fetchone()[
                    0
                ]
                for s in statuses
            }
            other = db.execute(
                "SELECT COUNT(*) FROM backup_outbox WHERE status NOT IN (?,?,?,?,?,?) OR status IS NULL",
                statuses,
            ).fetchone()[0]
            # MIN ignores NULLs and can hide malformed/future rows beside a valid row.
            invalid_wait_times = db.execute(
                "SELECT COUNT(*) FROM backup_outbox o JOIN backup_records r ON r.id=o.id "
                "WHERE o.status IN ('pending','sending') AND "
                "(typeof(r.created) NOT IN ('integer','real') OR r.created < 0 OR r.created > ?)",
                (now + 60,),
            ).fetchone()[0]
            oldest = db.execute(
                "SELECT MIN(r.created) FROM backup_outbox o JOIN backup_records r ON r.id=o.id WHERE o.status IN ('pending','sending')"
            ).fetchone()[0]
            unmatched = db.execute(
                "SELECT COUNT(*) FROM backup_outbox o LEFT JOIN backup_records r ON r.id=o.id WHERE r.id IS NULL"
            ).fetchone()[0]
        if other or unmatched or invalid_wait_times:
            return unknown("queue")
        if oldest is None and counts["pending"] + counts["sending"] > 0:
            return unknown("queue")
        age = None if oldest is None else now - oldest
        if age is not None and (age < -60 or not float("-inf") < age < float("inf")):
            return unknown("queue")
        bad = (
            counts["unknown"] > 0
            or counts["blocked"] > 0
            or counts["rejected"] > 0
            or (age is not None and age > 600)
        )
        return {
            "name": "queue",
            "status": "attention" if bad else "ok",
            "counts": counts,
            "oldest_wait_seconds": None if age is None else max(0, int(age)),
        }
    except Exception:
        return unknown("queue")


def summarize(checks, now):
    attention = any(c["status"] == "attention" for c in checks)
    uncertain = any(c["status"] == "unknown" for c in checks)
    code = 1 if attention else 2 if uncertain else 0
    return {
        "schema_version": 1,
        "checked_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "overall": ("ok", "attention", "unknown")[code],
        "has_attention": attention,
        "has_unknown": uncertain,
        "checks": checks,
        "limits": [
            "Same-host diagnosis is not an independent outage alert.",
            "COS status is the last unit exit, not a fresh object read-back.",
            "Endpoint health does not expose a separate worker heartbeat.",
            "SQLite reads can update WAL coordination sidecars, never message records.",
        ],
    }, code


def collect(config, backups, now=None):
    now = time.time() if now is None else now
    snapshot = check_snapshot(backups, now)
    checks = [
        check_health(),
        check_disk(),
        snapshot,
        {**check_disk(backups), "name": "backup_disk"},
    ]
    try:
        units = unit_properties()
    except Exception:
        units = {}
    for label, unit in zip(
        ("service", "backup_last_run", "cos_last_run", "backup_timer", "cos_timer"), UNITS + TIMERS
    ):
        result = check_unit(label, units.get(unit, {}), now)
        if (
            label == "cos_last_run"
            and result["status"] == "ok"
            and "age_seconds" in snapshot
            and result["age_seconds"] > snapshot["age_seconds"] + 60
        ):
            result.update(status="attention", reason="cos_run_predates_latest_snapshot")
        checks.append(result)
    try:
        queue = queue_path(config)
        checks.append(check_queue(queue, now))
        checks.append({**check_disk(queue.parent), "name": "data_disk"})
    except Exception:
        checks.extend([unknown("queue"), unknown("data_disk")])
    return summarize(checks, now)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/vox-team/runtime.json"))
    parser.add_argument("--backups", type=Path, default=Path("/var/lib/vox-team-backups"))
    args = parser.parse_args()
    report, code = collect(args.config, args.backups)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
