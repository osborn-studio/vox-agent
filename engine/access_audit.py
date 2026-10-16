"""Access audit storage for permissioned work-context queries."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from runtime.data_paths import resolve_vox_agent_data_dir

from .org_schema import migrate_org_schema, open_org_database


def _new_id() -> str:
    return uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AccessAuditRecord:
    org_id: str
    requester_user_id: str
    target_actor_id: str
    decision: str
    reason: str
    source_ids: list[str]
    target_scope: str = ""
    id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)


class AccessAuditStore:
    """Manages audit records for work-context access decisions."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or resolve_vox_agent_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._db: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._db = open_org_database(self.data_dir)
        self._migrate()

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None

    def insert_audit_record(self, record: AccessAuditRecord) -> None:
        self._ensure_connected()
        self._db.execute(
            """INSERT INTO access_audit
               (id, org_id, requester_user_id, target_actor_id, target_scope,
                decision, reason, source_ids, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.org_id,
                record.requester_user_id,
                record.target_actor_id,
                record.target_scope,
                record.decision,
                record.reason,
                json.dumps(record.source_ids),
                record.created_at,
            ),
        )
        self._db.commit()

    def list_audit_records(self, org_id: str, limit: int = 100) -> list[dict]:
        self._ensure_connected()
        rows = self._db.execute(
            """SELECT * FROM access_audit
               WHERE org_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (org_id, limit),
        ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            record["source_ids"] = json.loads(record["source_ids"] or "[]")
            records.append(record)
        return records

    def _ensure_connected(self) -> None:
        if self._db is None:
            self.connect()

    def _migrate(self) -> None:
        migrate_org_schema(self._db)
