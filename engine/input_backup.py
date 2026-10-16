"""Isolated input archive and transactional delivery outbox.

Single-host SQLite only. Records contain private text; use an encrypted local
volume. Never place this database or its WAL in a general diagnostic bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field


class BackupInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    eventId: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    targetId: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=8192)
    projectId: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    repoSource: Literal["manual"] | None = None
    repository: str | None = Field(default=None, max_length=200, pattern=r"^[a-zA-Z0-9_./-]+$")


@dataclass(frozen=True)
class Target:
    id: str
    version: int
    org_id: str
    chat_id: str
    allowed_users: frozenset[str]

    def __post_init__(self):
        if type(self.version) is not int or self.version < 1 or not self.chat_id.startswith("oc_"):
            raise ValueError("invalid target configuration")


class DeliveryPaused(Exception):
    pass


class BackupConflict(Exception):
    pass


# Wire contract values (see apps/desktop-tauri/test/fixtures/context-backup-wire-v2.json).
# The worker stops retrying an unknown delivery this many seconds after its first attempt.
RETRY_WINDOW_SECONDS = 3000
# Maximum UTF-8 size of the rendered group message, including the sender prefix.
RENDERED_MAX_BYTES = 8192


class BackupTooLarge(Exception):
    pass


class BackupStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Create privately before SQLite can write a single byte; existing stores
        # and sidecars may have been created under a permissive umask.
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists():
                sidecar.chmod(0o600)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS backup_records (
                id TEXT PRIMARY KEY, org TEXT NOT NULL, actor TEXT NOT NULL,
                event TEXT NOT NULL, fingerprint TEXT NOT NULL,
                rendered TEXT NOT NULL, created REAL NOT NULL, raw_payload TEXT NOT NULL,
                UNIQUE(org, actor, event)
            );
            CREATE TABLE IF NOT EXISTS backup_outbox (
                id TEXT PRIMARY KEY REFERENCES backup_records(id),
                target TEXT NOT NULL, version INTEGER NOT NULL, chat TEXT NOT NULL,
                delivery_uuid TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                first_attempt REAL, next_attempt REAL NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, message_id TEXT
            );
            CREATE INDEX IF NOT EXISTS backup_records_own_time
                ON backup_records(org,actor,created DESC,id DESC);
        """)

    def close(self):
        with self.lock:
            self.db.close()

    def count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM backup_records").fetchone()[0]

    @staticmethod
    def canonical_request(value):
        payload = value.model_dump()
        if value.projectId is None and value.repoSource is None:
            payload.pop("projectId")
            payload.pop("repoSource")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def replay(self, org, actor, value):
        fingerprint = hashlib.sha256(self.canonical_request(value).encode()).hexdigest()
        with self.lock:
            row = self.db.execute("SELECT fingerprint FROM backup_records WHERE org=? AND actor=? AND event=?", (org, actor, value.eventId)).fetchone()
            if row is None:
                return None
            if row["fingerprint"] != fingerprint:
                raise BackupConflict()
            return self.status(org, actor, value.eventId)

    def accept(self, org: str, actor: str, value: BackupInput, target: Target, *, sender_name: str | None = None, repository_snapshot: str | None = None, now=None):
        if target.org_id != org or actor not in target.allowed_users or target.id != value.targetId:
            raise PermissionError("target denied")
        canonical = self.canonical_request(value)
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        key = json.dumps([org, actor, value.eventId], separators=(",", ":"))
        record_id = str(uuid5(NAMESPACE_URL, "vox-backup-record:" + key))
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute(
                "SELECT fingerprint FROM backup_records WHERE id=?", (record_id,)
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise BackupConflict()
                return self.status(org, actor, value.eventId)
            if value.projectId is not None and (
                value.repoSource != "manual" or not isinstance(repository_snapshot, str)
                or len(repository_snapshot) > 200
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository_snapshot)
                or value.repository not in {None, repository_snapshot}
            ):
                raise ValueError("invalid project snapshot")
            created = time.time() if now is None else now
            # Attribution is supplied by the trusted server, never the request body.
            # Snapshot it once so retries after a profile rename remain idempotent.
            name = " ".join((sender_name or actor).split())
            name = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
            name = (name[:120].strip() or actor).replace("<", "＜").replace(">", "＞")
            text = value.text.replace("<", "＜").replace(">", "＞")
            rendered = f"发言者：{name}\n\n{text}"
            if len(rendered.encode("utf-8")) > RENDERED_MAX_BYTES:
                raise BackupTooLarge()
            delivery_uuid = str(
                uuid5(
                    NAMESPACE_URL,
                    json.dumps(
                        [
                            "vox-backup-delivery-v1",
                            org,
                            actor,
                            value.eventId,
                            target.id,
                            target.version,
                            target.chat_id,
                        ]
                    ),
                )
            )
            stored_payload = canonical
            if value.projectId is not None:
                snapshot = json.loads(canonical)
                snapshot["resolvedRepository"] = repository_snapshot
                stored_payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            self.db.execute(
                "INSERT INTO backup_records VALUES (?,?,?,?,?,?,?,?)",
                (record_id, org, actor, value.eventId, fingerprint, rendered, created, stored_payload),
            )
            self.db.execute(
                """INSERT INTO backup_outbox
                (id,target,version,chat,delivery_uuid) VALUES (?,?,?,?,?)""",
                (record_id, target.id, target.version, target.chat_id, delivery_uuid),
            )
        return self.status(org, actor, value.eventId)

    def status(self, org, actor, event):
        with self.lock:
            row = self.db.execute(
                """SELECT r.event AS eventId, o.status, o.message_id AS messageId, r.raw_payload AS rawPayload
                FROM backup_records r JOIN backup_outbox o ON r.id=o.id
                WHERE r.org=? AND r.actor=? AND r.event=?""",
                (org, actor, event),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            raw = result.pop("rawPayload")
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("invalid stored attribution")
                project = payload.get("projectId")
                if project is not None:
                    repository = payload.get("resolvedRepository")
                    if not isinstance(project, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", project):
                        raise ValueError("invalid stored attribution")
                    if not isinstance(repository, str) or len(repository) > 200 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
                        raise ValueError("invalid stored attribution")
                    result["attribution"] = {"version": 1, "projectId": project, "repositoryId": repository}
            except (ValueError, TypeError, RecursionError):
                # Do not echo damaged raw text, paths or credentials, or confirm it.
                result["attribution"] = {"version": 1, "valid": False}
            return result

    def project_attribution(self, record_id):
        """Only attribution fields cross into worker authorization, never text."""
        with self.lock:
            row = self.db.execute("SELECT raw_payload FROM backup_records WHERE id=?", (record_id,)).fetchone()
            if row is None:
                raise LookupError("record unavailable")
            payload = json.loads(row["raw_payload"])
            if not isinstance(payload, dict):
                raise ValueError("invalid attribution")
            project = payload.get("projectId")
            repository = payload.get("resolvedRepository")
            if (project is not None and not isinstance(project, str)) or (repository is not None and not isinstance(repository, str)):
                raise ValueError("invalid attribution")
            return project, repository

    def recover(self):
        """Only call after obtaining the worker process lock."""
        with self.lock, self.db:
            self.db.execute("UPDATE backup_outbox SET status='unknown' WHERE status='sending'")

    def claim(self, *, now=None):
        now = time.time() if now is None else now
        with self.lock, self.db:
            # IMMEDIATE serializes competing connections, not just this instance.
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                """SELECT o.*, r.rendered, r.org, r.actor FROM backup_outbox o
                JOIN backup_records r ON r.id=o.id
                WHERE status IN ('pending','unknown') AND next_attempt<=?
                AND (first_attempt IS NULL OR (? >= first_attempt AND ?-first_attempt<?))
                ORDER BY r.created, r.id LIMIT 1""",
                (now, now, now, RETRY_WINDOW_SECONDS),
            ).fetchone()
            if row is None:
                return None
            self.db.execute(
                """UPDATE backup_outbox SET status='sending',
                first_attempt=COALESCE(first_attempt,?), attempts=attempts+1 WHERE id=?""",
                (now, row["id"]),
            )
            job = dict(row)
            job["previous_first_attempt"] = row["first_attempt"]
            job["first_attempt"] = row["first_attempt"] if row["first_attempt"] is not None else now
            job["attempts"] += 1
            return job

    def defer(self, job, *, now=None):
        """No message POST was attempted this time. Preserve any earlier uncertainty."""
        now = time.time() if now is None else now
        previous = job["previous_first_attempt"]
        with self.lock, self.db:
            self.db.execute(
                """UPDATE backup_outbox SET status=?, first_attempt=?, next_attempt=?
                WHERE id=? AND status='sending' """,
                ("unknown" if previous is not None else "pending", previous, now + 60, job["id"]),
            )

    def block(self, job, *, now=None):
        """Authorization was denied before any POST in this attempt.

        `first_attempt` keeps only the history from earlier attempts: it stays
        NULL when no POST was ever attempted, and keeps the earlier time when a
        previous attempt may have reached Feishu. Operator recovery relies on
        this to tell a never-sent blocked record from one that needs checking.
        """
        now = time.time() if now is None else now
        with self.lock, self.db:
            self.db.execute(
                """UPDATE backup_outbox SET status='blocked', first_attempt=?, next_attempt=?
                WHERE id=? AND status='sending' """,
                (job["previous_first_attempt"], now + 60, job["id"]),
            )

    def finish(self, record_id, status, *, message_id=None, now=None):
        if status not in {"delivered", "unknown", "rejected", "blocked"}:
            raise ValueError("invalid delivery result")
        now = time.time() if now is None else now
        with self.lock, self.db:
            self.db.execute(
                """UPDATE backup_outbox SET status=?,message_id=?,next_attempt=?
                WHERE id=? AND status='sending' """,
                (status, message_id, now + 60, record_id),
            )
