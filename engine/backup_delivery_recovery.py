"""Local operator recovery for team backup deliveries the worker will not retry.

Preview by default. Two cases are recoverable:

* ``blocked``: the worker's pre-send authorization failed. If no earlier attempt
  ever posted (``first_attempt`` is NULL, see ``BackupStore.block``), it was never
  sent and can be queued again without risk of a duplicate. If an earlier attempt
  may have posted, or the record predates that bookkeeping, it is treated like
  ``unknown`` and needs ``--attest-not-delivered``.
* ``unknown`` past the worker retry window (``RETRY_WINDOW_SECONDS`` since the
  first attempt): a post may or may not have reached Feishu. The operator must
  check the group first and pass ``--attest-not-delivered``; requeuing a message
  that was delivered would post it twice.

Applying requires the worker's own process lock (so the worker is not running),
is bounded to explicit eventIds of one org/actor, compares each record's state
with the preview before changing it, and writes an actor-bearing audit row in
the same transaction. Message text is never read or printed. Re-running is a
no-op. See docs/decisions/2026-10-08-backup-delivery-recovery-v1.md.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from engine.input_backup import RETRY_WINDOW_SECONDS, BackupStore

MAX_EVENTS = 50
_EVENT = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_OPERATOR = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS backup_delivery_recovery_audit (
    id INTEGER PRIMARY KEY,
    change_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    org TEXT NOT NULL,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    from_status TEXT NOT NULL,
    first_attempt REAL,
    operator TEXT NOT NULL,
    os_uid INTEGER NOT NULL,
    reason TEXT NOT NULL,
    attested_not_delivered INTEGER NOT NULL,
    at REAL NOT NULL
)
"""


class RecoveryError(Exception):
    """Refusal with a stable code; never carries message content."""


def _validate_selection(org: str, actor: str, events: list[str]) -> list[str]:
    if not (isinstance(org, str) and 0 < len(org) <= 128 and isinstance(actor, str) and 0 < len(actor) <= 128):
        raise RecoveryError("INVALID_SCOPE")
    if not events:
        raise RecoveryError("NO_EVENTS")
    unique = list(dict.fromkeys(events))
    if len(unique) > MAX_EVENTS:
        raise RecoveryError("TOO_MANY_EVENTS")
    if not all(isinstance(event, str) and _EVENT.match(event) for event in unique):
        raise RecoveryError("INVALID_EVENT_ID")
    return unique


def _classify(row, now: float) -> dict:
    if row is None:
        return {"eligible": False, "reason": "NOT_FOUND"}
    state, first = row["status"], row["first_attempt"]
    if state == "blocked":
        # NULL first_attempt: no POST was ever attempted (BackupStore.block).
        return {"eligible": True, "requires_attestation": first is not None}
    if state == "unknown":
        if first is None:
            return {"eligible": False, "reason": "UNEXPECTED_STATE"}
        if now - first < RETRY_WINDOW_SECONDS:
            return {"eligible": False, "reason": "WORKER_STILL_RETRYING"}
        return {"eligible": True, "requires_attestation": True}
    return {"eligible": False, "reason": "NOT_RECOVERABLE_STATUS"}


def _canonical(db_path) -> Path:
    try:
        return Path(db_path).resolve(strict=True)
    except (FileNotFoundError, RuntimeError):
        raise RecoveryError("DATABASE_NOT_FOUND") from None


def _read(db, org: str, actor: str, event: str):
    return db.execute(
        """SELECT r.id AS record_id, o.status, o.first_attempt FROM backup_records r
        JOIN backup_outbox o ON r.id=o.id WHERE r.org=? AND r.actor=? AND r.event=?""",
        (org, actor, event),
    ).fetchone()


def plan_recovery(db_path, org: str, actor: str, events: list[str], *, now: float | None = None) -> dict:
    """Read-only classification. Opens the existing database read-only and
    never creates, migrates or re-permissions files."""
    selected = _validate_selection(org, actor, events)
    now = time.time() if now is None else now
    path = _canonical(db_path)
    # A WAL database opened read-only still creates -wal/-shm sidecars. When they
    # are absent the database is fully checkpointed with no active writer, so
    # open it immutable and create nothing; otherwise they already exist. The
    # preview is advisory: apply_recovery re-plans under the worker lock.
    wal, shm = Path(str(path) + "-wal").exists(), Path(str(path) + "-shm").exists()
    if wal and not shm:
        # Reading would make SQLite create the -shm file: stop the service
        # (which checkpoints the WAL) before previewing.
        raise RecoveryError("DATABASE_IN_USE")
    uri = f"file:{path}?mode=ro" + ("" if wal or shm else "&immutable=1")
    try:
        db = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        raise RecoveryError("DATABASE_UNREADABLE") from None
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        items = []
        for event in selected:
            row = _read(db, org, actor, event)
            item = {"eventId": event, "status": row["status"] if row else None, **_classify(row, now)}
            if row is not None:
                item["_record_id"], item["_first_attempt"] = row["record_id"], row["first_attempt"]
            items.append(item)
    except sqlite3.Error:
        raise RecoveryError("DATABASE_UNREADABLE") from None
    finally:
        db.close()
    return {"mode": "preview", "org": org, "actor": actor, "retry_window_seconds": RETRY_WINDOW_SECONDS,
            "items": items}


def _public(plan: dict) -> dict:
    return {**plan, "items": [{k: v for k, v in item.items() if not k.startswith("_")} for item in plan["items"]]}


def apply_recovery(db_path, org: str, actor: str, events: list[str], *, operator: str, reason: str,
                   attest_not_delivered: bool = False, now: float | None = None) -> dict:
    if not (isinstance(operator, str) and _OPERATOR.match(operator)):
        raise RecoveryError("OPERATOR_REQUIRED")
    if not (isinstance(reason, str) and 0 < len(reason) <= 500 and reason.isprintable() and reason.strip()):
        raise RecoveryError("REASON_REQUIRED")
    now = time.time() if now is None else now
    # Lock the same canonical path the worker locks (BackupStore resolves its path),
    # so a symlink or alias cannot yield a second, independent lock.
    db_path = _canonical(db_path)
    lock = open(str(db_path) + ".worker.lock", "a")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RecoveryError("WORKER_RUNNING") from None
        plan = plan_recovery(db_path, org, actor, events, now=now)
        eligible = [item for item in plan["items"] if item["eligible"]]
        if any(item["requires_attestation"] for item in eligible) and not attest_not_delivered:
            raise RecoveryError("ATTESTATION_REQUIRED")
        change_id = str(uuid.uuid4())
        requeued, skipped = [], []
        store = BackupStore(db_path)
        try:
            with store.lock, store.db:
                store.db.execute("BEGIN IMMEDIATE")
                store.db.execute(_AUDIT_SCHEMA)
                for item in eligible:
                    # Compare-and-set against the previewed state.
                    changed = store.db.execute(
                        """UPDATE backup_outbox SET status='pending', first_attempt=NULL, next_attempt=0
                        WHERE id=? AND status=? AND first_attempt IS ?""",
                        (item["_record_id"], item["status"], item["_first_attempt"]),
                    ).rowcount
                    if changed != 1:
                        skipped.append(item["eventId"])
                        continue
                    store.db.execute(
                        """INSERT INTO backup_delivery_recovery_audit (change_id, record_id, org, actor, event,
                        from_status, first_attempt, operator, os_uid, reason, attested_not_delivered, at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (change_id, item["_record_id"], org, actor, item["eventId"], item["status"],
                         item["_first_attempt"], operator, os.geteuid(), reason,
                         int(bool(attest_not_delivered and item["requires_attestation"])), now),
                    )
                    requeued.append(item["eventId"])
        finally:
            store.close()
    finally:
        lock.close()
    return {**_public(plan), "mode": "applied", "change_id": change_id if requeued else None,
            "requeued": requeued, "skipped_changed": skipped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--org", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--event", action="append", default=[], help="repeatable; at most %d" % MAX_EVENTS)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--operator")
    parser.add_argument("--reason")
    parser.add_argument("--attest-not-delivered", action="store_true",
                        help="required for unknown records: the operator checked the group and the message is absent")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            result = apply_recovery(args.db, args.org, args.actor, args.event, operator=args.operator or "",
                                    reason=args.reason or "", attest_not_delivered=args.attest_not_delivered)
        else:
            result = _public(plan_recovery(args.db, args.org, args.actor, args.event))
    except RecoveryError as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
