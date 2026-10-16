"""Private durable conversation submissions; deliberately independent of BackupStore."""

import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_BYTES = 20_000
RETRY_WINDOW = 3000  # 50 minutes: ten-minute margin inside documented one-hour UUID dedup.
MAX_ATTEMPTS = 5
MAX_PENDING_AGE = 86400


class SendingError(Exception):
    def __init__(self, code):
        allowed = {
            "EVENT_PAYLOAD_CONFLICT",
            "RENDERED_MESSAGE_TOO_LARGE",
            "INVALID_TEXT",
            "QUEUE_FULL",
            "WORKER_ALREADY_RUNNING",
            "PRIVATE_STORAGE_REQUIRED",
        }
        super().__init__(code if code in allowed else "SENDING_UNAVAILABLE")


class SendInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    eventId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    text: str = Field(min_length=1, max_length=MAX_BYTES)

    @field_validator("text")
    @classmethod
    def valid_text(cls, value):
        if not value.strip() or any(unicodedata.category(c) == "Cs" or c == "\x00" for c in value):
            raise ValueError("INVALID_TEXT")
        return value


def render_text(name, text):
    name = (
        "".join(
            c
            for c in name
            if unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Zl", "Zp") and c not in "【】"
        )[:40].strip()
        or "User"
    )

    # Escape markup explicitly; fullwidth @ cannot be interpreted as a real mention.
    def escape(value):
        return (
            value.replace("<", "＜").replace(">", "＞").replace("@", "＠")
        )

    # Body is untrusted text, never an authorship source. Preserve quotes/code.
    text = "".join("�" if (unicodedata.category(c) == "Cc" and c not in "\n\t")
                   or unicodedata.bidirectional(c) in
                   ("LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI")
                   else c for c in text)
    result = "【" + escape(name) + "】" + escape(text)
    try:
        size = len(result.encode("utf-8"))
    except UnicodeError:
        size = MAX_BYTES + 1
    if size > MAX_BYTES:
        raise SendingError("RENDERED_MESSAGE_TOO_LARGE")
    return result


def scope(identity):
    return tuple(identity[k] for k in ("app_id", "tenant", "org_id", "user_id"))


def private_file(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise SendingError("PRIVATE_STORAGE_REQUIRED")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    os.close(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SendingError("PRIVATE_STORAGE_REQUIRED")
    return path


class ConversationStore:
    def __init__(self, path, *, clock=time.time, monotonic=time.monotonic,
                 principal_active_limit=100, principal_total_limit=1000,
                 global_active_limit=1000, global_total_limit=10000,
                 principal_byte_limit=32_000_000, global_byte_limit=320_000_000):
        self.path = private_file(path)
        self.clock, self.monotonic = clock, monotonic
        self.highwater = clock()
        self.deadlines = {}
        self.last_principal = None
        self.limits = (principal_active_limit, principal_total_limit,
                       global_active_limit, global_total_limit,
                       principal_byte_limit, global_byte_limit)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS submissions (
            app_id TEXT, tenant TEXT, org_id TEXT, user_id TEXT, event TEXT,
            conversation TEXT, target TEXT, open_id TEXT, text TEXT, rendered TEXT,
            delivery_uuid TEXT UNIQUE, status TEXT, reason TEXT, message_id TEXT, chat_id TEXT,
            created REAL, first_attempt REAL, next_attempt REAL, attempts INTEGER DEFAULT 0,
            checks INTEGER DEFAULT 0, ambiguous INTEGER DEFAULT 0,
            render_version INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(app_id, tenant, org_id, user_id, event));
          CREATE TABLE IF NOT EXISTS dm_bindings (
            app_id TEXT, tenant TEXT, org_id TEXT, user_id TEXT, chat_id TEXT,
            PRIMARY KEY(app_id, tenant, org_id, user_id));
        """)
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(submissions)")}
        if "author_name" not in columns:
            self.db.execute("ALTER TABLE submissions ADD COLUMN author_name TEXT")
        if "final" not in columns:
            self.db.execute("ALTER TABLE submissions ADD COLUMN final INTEGER NOT NULL DEFAULT 0")
        self.db.execute("UPDATE submissions SET final=1 WHERE status IN ('sent','failed','membership_lost')")
        self.db.commit()
        # Recovery is performed by the sole worker after obtaining its process lock.
        # The runtime starts recovery before exposing any authenticated routes.

    def close(self):
        with self.lock:
            self.db.close()

    @staticmethod
    def key(job):
        return (*scope(job), job["event"])

    @staticmethod
    def public(row):
        value = {
            "eventId": row["event"],
            "conversationId": row["conversation"],
            "status": row["status"],
            "reason": row["reason"],
            "final": bool(row["final"]),
        }
        if row["status"] == "sent":
            value["messageId"] = row["message_id"]
        return value

    def accept(self, identity, conversation, target, value):
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT * FROM submissions WHERE app_id=? AND tenant=? AND org_id=? AND user_id=? AND event=?",
                (*scope(identity), value.eventId),
            ).fetchone()
            if row:
                if row["conversation"] != conversation or row["target"] != target or row["text"] != value.text:
                    raise SendingError("EVENT_PAYLOAD_CONFLICT")
                return self.public(row)
            rendered = render_text(identity["name"], value.text)
            # Keep originals for Phase 5; no implicit retention deletion. Reserve
            # metadata space in addition to both UTF-8 body representations.
            size = len(value.text.encode()) + len(rendered.encode()) + 4096
            rows = self.db.execute("""SELECT app_id,tenant,org_id,user_id,final,
                length(CAST(text AS BLOB))+length(CAST(rendered AS BLOB))+4096 AS bytes
                FROM submissions""").fetchall()
            own = [r for r in rows if scope(r) == scope(identity)]
            pa, pt, ga, gt, pb, gb = self.limits
            if (sum(not r["final"] for r in own) >= pa or len(own) >= pt
                    or sum(not r["final"] for r in rows) >= ga or len(rows) >= gt
                    or sum(r["bytes"] for r in own) + size > pb
                    or sum(r["bytes"] for r in rows) + size > gb):
                raise SendingError("QUEUE_FULL")
            self.db.execute(
                """INSERT INTO submissions
                (app_id,tenant,org_id,user_id,event,conversation,target,open_id,text,rendered,
                 delivery_uuid,status,reason,created,next_attempt,render_version,author_name)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending','QUEUED',?,?,2,?)""",
                (
                    *scope(identity),
                    value.eventId,
                    conversation,
                    target,
                    identity["open_id"],
                    value.text,
                    rendered,
                    str(uuid.uuid4()),
                    self.clock(),
                    self.clock(),
                    identity["name"][:80],
                ),
            )
            return self.receipt(identity, conversation, value.eventId)

    def receipt(self, identity, conversation, event):
        with self.lock:
            row = self.db.execute(
                """SELECT * FROM submissions WHERE app_id=? AND tenant=?
                AND org_id=? AND user_id=? AND event=? AND conversation=?""",
                (*scope(identity), event, conversation),
            ).fetchone()
            return self.public(row) if row else None

    def recover(self):
        with self.lock, self.db:
            # A wall clock cannot prove elapsed real time across a reboot.
            self.db.execute("""UPDATE submissions SET
                status=CASE WHEN ambiguous=1 OR status IN ('sending','unknown')
                            THEN 'unknown' ELSE 'failed' END,
                ambiguous=CASE WHEN status IN ('sending','unknown') THEN 1 ELSE ambiguous END,
                final=1, reason='RESTART_RETIRED'
                WHERE final=0 AND (first_attempt IS NOT NULL OR status IN ('sending','unknown'))""")
            self.deadlines.clear()

    def _sweep(self):
        now = self.clock()
        rollback = now < self.highwater
        self.highwater = max(self.highwater, now)
        rows = self.db.execute("SELECT * FROM submissions WHERE final=0").fetchall()
        for row in rows:
            reason = None
            if row["first_attempt"] is not None:
                deadline = self.deadlines.get(self.key(row))
                if rollback:
                    reason = "CLOCK_UNTRUSTED"
                elif deadline is None:
                    reason = "RESTART_RETIRED"
                elif self.monotonic() >= deadline or now >= row["first_attempt"] + RETRY_WINDOW:
                    reason = "WINDOW_EXPIRED"
            if reason is None and (row["attempts"] >= MAX_ATTEMPTS or row["checks"] >= 10):
                reason = "RETRY_EXHAUSTED"
            if reason is None and row["first_attempt"] is None and now - row["created"] >= MAX_PENDING_AGE:
                reason = "STALE_PENDING"
            if reason:
                self.update(dict(row), "failed", reason, final=True)
        return now

    def next_job(self):
        with self.lock, self.db:
            now = self._sweep()
            # Lexicographic round-robin principals; oldest eligible row per user.
            rows = self.db.execute("""SELECT * FROM submissions WHERE final=0
                AND status IN ('pending','unknown') AND next_attempt<=?
                ORDER BY app_id,tenant,org_id,user_id,created,event""", (now,)).fetchall()
            if not rows:
                return None
            row = next((r for r in rows if self.last_principal is None
                        or scope(r) > self.last_principal), rows[0])
            self.last_principal = scope(row)
            return dict(row)

    def update(self, job, status, reason, *, final=None):
        with self.lock, self.db:
            current = self.db.execute("""SELECT * FROM submissions WHERE
                app_id=? AND tenant=? AND org_id=? AND user_id=? AND event=?""", self.key(job)).fetchone()
            if current["final"]:
                return
            if final is None:
                final = status in ("failed", "membership_lost", "sent")
            # Sending means ambiguous only on crash; a known rejection of this
            # attempt may be definitive, but never resolves a prior ambiguity.
            if current["ambiguous"] and status != "sent":
                status = "unknown"
            checks = current["checks"] + 1
            if not final and (checks >= 10 or current["attempts"] >= MAX_ATTEMPTS):
                final, reason = True, "RETRY_EXHAUSTED"
                status = "unknown" if status == "unknown" else "failed"
            self.db.execute("""UPDATE submissions SET status=?, reason=?, checks=?, final=?,
                ambiguous=MAX(ambiguous,?), next_attempt=? WHERE
                app_id=? AND tenant=? AND org_id=? AND user_id=? AND event=?""",
                (status, reason, checks, int(final), int(status == "unknown"),
                 self.clock() + min(300, 60 * checks), *self.key(job)))

    def begin(self, job):
        with self.lock, self.db:
            now = self._sweep()
            current = self.db.execute("""SELECT * FROM submissions WHERE
                app_id=? AND tenant=? AND org_id=? AND user_id=? AND event=?""", self.key(job)).fetchone()
            if current["final"]:
                return False
            if current["first_attempt"] is None:
                self.deadlines[self.key(job)] = self.monotonic() + RETRY_WINDOW
            self.db.execute("""UPDATE submissions SET status='sending', attempts=attempts+1,
                first_attempt=COALESCE(first_attempt,?) WHERE
                app_id=? AND tenant=? AND org_id=? AND user_id=? AND event=?""", (now, *self.key(job)))
            return True

    def complete(self, job, response):
        valid = isinstance(response, dict) and all(
            isinstance(response.get(k), str) and re.fullmatch(pattern, response[k])
            for k, pattern in (
                ("message_id", r"om_[A-Za-z0-9_-]{1,253}"),
                ("chat_id", r"oc_[A-Za-z0-9]{1,253}"),
            )
        )
        with self.lock, self.db:
            well_formed = valid
            if valid and job["target"] == "dm":
                row = self.db.execute(
                    "SELECT chat_id FROM dm_bindings WHERE app_id=? AND tenant=? AND org_id=? AND user_id=?",
                    scope(job),
                ).fetchone()
                valid = row is None or row[0] == response["chat_id"]
                if valid:
                    self.db.execute(
                        "INSERT OR IGNORE INTO dm_bindings VALUES (?,?,?,?,?)",
                        (*scope(job), response["chat_id"]),
                    )
            elif valid:
                valid = response["chat_id"] == job["target"]
            if not valid:
                self.update(job, "unknown", "RECEIPT_MISMATCH" if well_formed else "INVALID_RECEIPT",
                            final=well_formed)
                return
            self.db.execute(
                """UPDATE submissions SET status='sent', reason='DELIVERED', final=1,
                message_id=?,chat_id=? WHERE app_id=? AND tenant=? AND org_id=? AND user_id=? AND event=?""",
                (response["message_id"], response["chat_id"], *self.key(job)),
            )

    def dm_binding(self, identity):
        with self.lock:
            row = self.db.execute("SELECT chat_id FROM dm_bindings WHERE app_id=? AND tenant=? AND org_id=? AND user_id=?", scope(identity)).fetchone()
            if row is None:
                return None
            # Legacy bindings carry no open_id. Recover proof only from immutable
            # successful receipts; missing/ambiguous ownership fails to own-only.
            owners = self.db.execute(
                """SELECT DISTINCT open_id FROM submissions WHERE app_id=? AND tenant=?
                AND org_id=? AND user_id=? AND target='dm' AND chat_id=? AND status='sent' LIMIT 2""",
                (*scope(identity), row[0]),
            ).fetchall()
            return row[0] if len(owners) == 1 and owners[0][0] == identity["open_id"] else None

    def read_highwater(self):
        with self.lock:
            return self.db.execute("SELECT COALESCE(MAX(rowid),0) FROM submissions").fetchone()[0]

    def original_page(self, identity, conversation, target, highwater, before, limit):
        with self.lock:
            sql = """SELECT * FROM submissions WHERE app_id=? AND tenant=? AND org_id=? AND user_id=?
                AND conversation=? AND target=? AND rowid<=?"""
            args = [*scope(identity), conversation, target, highwater]
            if before:
                sql += " AND (created < ? OR (created = ? AND event < ?))"
                args += [before[0], before[0], before[1]]
            return self.db.execute(sql + " ORDER BY created DESC,event DESC LIMIT ?", (*args, limit)).fetchall()

    def message_actor(self, identity, target, chat, message):
        with self.lock:
            sql = """SELECT event,user_id,author_name FROM submissions WHERE app_id=? AND tenant=? AND org_id=?
                AND target=? AND chat_id=? AND message_id=? AND status='sent'"""
            args = [identity["app_id"], identity["tenant"], identity["org_id"], target, chat, message]
            if target == "dm":
                sql += " AND user_id=?"
                args.append(identity["user_id"])
            rows = self.db.execute(sql + " LIMIT 2", args).fetchall()
            return rows[0] if len(rows) == 1 else None
