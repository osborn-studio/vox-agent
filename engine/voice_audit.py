"""Bounded, optionally durable audit log for voice and admin actions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from runtime.sqlite_policy import configure_sqlite_connection


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VoiceAuditLog:
    """Record structured events in memory or in a SQLite database.

    Raw transcripts and credentials must never be passed in ``metadata``.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self._lock = RLock()
        self._db: sqlite3.Connection | None = None
        if db_path is not None:
            resolved = Path(db_path)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(resolved, timeout=5, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            configure_sqlite_connection(self._db)
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_audit_event (
                    id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    actor_id TEXT,
                    workspace_id TEXT,
                    target_id TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_admin_audit_created
                    ON admin_audit_event(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_admin_audit_actor
                    ON admin_audit_event(actor_id, created_at DESC);
                """
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def record(
        self,
        action: str,
        actor_id: str | None = None,
        workspace_id: str | None = None,
        target_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        event = {
            "id": f"aud_{uuid4().hex[:12]}",
            "action": action,
            "actorId": actor_id,
            "workspaceId": workspace_id,
            "targetId": target_id,
            "metadata": metadata or {},
            "createdAt": _now_iso(),
        }
        with self._lock:
            if self._db is None:
                self.events.append(event)
            else:
                self._db.execute(
                    """INSERT INTO admin_audit_event
                       (id, action, actor_id, workspace_id, target_id, metadata, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event["id"],
                        event["action"],
                        event["actorId"],
                        event["workspaceId"],
                        event["targetId"],
                        json.dumps(event["metadata"], separators=(",", ":")),
                        event["createdAt"],
                    ),
                )
                self._db.commit()
        return event

    def list_events(self, limit: int = 50) -> list:
        bounded_limit = max(1, min(int(limit), 1000))
        with self._lock:
            if self._db is None:
                return list(reversed(self.events))[:bounded_limit]
            rows = self._db.execute(
                """SELECT id, action, actor_id, workspace_id, target_id, metadata, created_at
                   FROM admin_audit_event
                   ORDER BY created_at DESC, id DESC
                   LIMIT ?""",
                (bounded_limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "action": row["action"],
                "actorId": row["actor_id"],
                "workspaceId": row["workspace_id"],
                "targetId": row["target_id"],
                "metadata": json.loads(row["metadata"] or "{}"),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]
