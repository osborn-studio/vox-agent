"""Work event models and storage for organization collaboration."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.data_paths import resolve_vox_agent_data_dir

from .redaction import redact_secrets
from .org_schema import migrate_org_schema, open_org_database


def _new_id() -> str:
    return uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkEventSourceType(str, Enum):
    VOICE = "voice"
    AGENT_TASK_MESSAGE = "agent_task_message"
    TERMINAL = "terminal"
    GIT = "git"
    MANUAL = "manual"


class WorkEventVisibility(str, Enum):
    PRIVATE = "private"
    TEAM_SUMMARY = "team_summary"
    TEAM_RAW = "team_raw"
    PROJECT_SUMMARY = "project_summary"
    PROJECT_RAW = "project_raw"
    ORG_SUMMARY = "org_summary"
    ADMIN_AUDIT = "admin_audit"


class RetentionClass(str, Enum):
    EPHEMERAL = "ephemeral"
    STANDARD = "standard"
    SENSITIVE = "sensitive"
    AUDIT = "audit"


@dataclass
class WorkEvent:
    org_id: str
    actor_type: str
    actor_id: str
    source_type: WorkEventSourceType
    body: str
    team_id: str | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    visibility: WorkEventVisibility = WorkEventVisibility.TEAM_RAW
    retention_class: RetentionClass = RetentionClass.STANDARD
    metadata: dict[str, Any] = field(default_factory=dict)
    redaction_reasons: list[str] = field(default_factory=list)
    source_hash: str = ""
    id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)


class WorkEventStore:
    """Manages persisted normalized work events."""

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

    def insert_work_event(self, event: WorkEvent) -> None:
        self._ensure_connected()
        redaction = redact_secrets(event.body)
        reasons = event.redaction_reasons or redaction.reasons
        source_hash = event.source_hash or redaction.source_hash

        self._db.execute(
            """INSERT INTO work_event
               (id, org_id, team_id, project_id, workspace_id, actor_type,
                actor_id, source_type, visibility, retention_class, body,
                metadata, redaction_reasons, source_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.org_id,
                event.team_id,
                event.project_id,
                event.workspace_id,
                event.actor_type,
                event.actor_id,
                event.source_type.value,
                event.visibility.value,
                event.retention_class.value,
                redaction.body,
                json.dumps(event.metadata),
                json.dumps(reasons),
                source_hash,
                event.created_at,
            ),
        )
        self._db.commit()

    def get_work_event(self, event_id: str) -> dict | None:
        self._ensure_connected()
        row = self._db.execute(
            "SELECT * FROM work_event WHERE id = ?", (event_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"] or "{}")
        result["redaction_reasons"] = json.loads(result["redaction_reasons"] or "[]")
        return result

    def list_work_events(
        self,
        org_id: str,
        team_id: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        self._ensure_connected()
        sql = "SELECT * FROM work_event WHERE org_id = ?"
        params: list[Any] = [org_id]
        if team_id:
            sql += " AND team_id = ?"
            params.append(team_id)
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._db.execute(sql, params).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["metadata"] = json.loads(event["metadata"] or "{}")
            event["redaction_reasons"] = json.loads(event["redaction_reasons"] or "[]")
            events.append(event)
        return events

    def _ensure_connected(self) -> None:
        if self._db is None:
            self.connect()

    def _migrate(self) -> None:
        migrate_org_schema(self._db)
