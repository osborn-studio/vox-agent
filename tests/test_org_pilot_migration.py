"""Recoverable migration from the org-collab pilot's split databases."""

from __future__ import annotations

import json
import sqlite3

from engine.access_audit import AccessAuditStore
from engine.app_context import create_app_context
from engine.org_migration import migrate_org_pilot_data, rollback_org_pilot_data
from engine.org_models import Organization
from engine.org_schema import ORG_SCHEMA_VERSION
from engine.org_store import OrgStore
from engine.work_events import WorkEventStore


def _create_legacy_databases(data_dir) -> None:
    with sqlite3.connect(data_dir / "work_events.db") as db:
        db.executescript(
            """
            CREATE TABLE work_event (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                team_id TEXT,
                project_id TEXT,
                workspace_id TEXT,
                actor_type TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                visibility TEXT NOT NULL,
                retention_class TEXT NOT NULL,
                body TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                redaction_reasons TEXT NOT NULL DEFAULT '[]',
                source_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO work_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "event-legacy",
                "pilot-org",
                None,
                None,
                None,
                "user",
                "pilot-user",
                "manual",
                "team_raw",
                "standard",
                "legacy event",
                json.dumps({"status": "completed"}),
                "[]",
                "digest",
                "2026-07-17T10:00:00+00:00",
            ),
        )

    with sqlite3.connect(data_dir / "access_audit.db") as db:
        db.executescript(
            """
            CREATE TABLE access_audit (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                requester_user_id TEXT NOT NULL,
                target_actor_id TEXT NOT NULL,
                target_scope TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                source_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO access_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "audit-legacy",
                "pilot-org",
                "pilot-user",
                "other-user",
                "team_raw",
                "allowed",
                "team_raw_member_access",
                '["event-legacy"]',
                "2026-07-17T10:01:00+00:00",
            ),
        )


def _seed_target_identity(data_dir) -> None:
    store = OrgStore(data_dir)
    store.insert_organization(Organization(id="pilot-org", name="Pilot"))
    store.close()


def test_dry_run_reports_rows_without_mutating_sources_or_target(tmp_path):
    _seed_target_identity(tmp_path)
    _create_legacy_databases(tmp_path)
    target_before = (tmp_path / "org.db").read_bytes()

    result = migrate_org_pilot_data(tmp_path, dry_run=True)

    assert result["dryRun"] is True
    assert result["executed"] is False
    assert result["candidates"] == {"workEvents": 1, "accessAudits": 1}
    assert result["imported"] == {"workEvents": 0, "accessAudits": 0}
    assert result["manifestPath"] is None
    assert (tmp_path / "work_events.db").exists()
    assert (tmp_path / "access_audit.db").exists()
    assert (tmp_path / "org.db").read_bytes() == target_before


def test_execute_is_idempotent_and_rollback_restores_split_pilot_files(tmp_path):
    _seed_target_identity(tmp_path)
    _create_legacy_databases(tmp_path)

    result = migrate_org_pilot_data(tmp_path, dry_run=False)

    assert result["executed"] is True
    assert result["imported"] == {"workEvents": 1, "accessAudits": 1}
    assert result["manifestPath"]
    assert not (tmp_path / "work_events.db").exists()
    assert not (tmp_path / "access_audit.db").exists()

    events = WorkEventStore(tmp_path)
    audits = AccessAuditStore(tmp_path)
    assert events.get_work_event("event-legacy")["body"] == "legacy event"
    assert audits.list_audit_records("pilot-org")[0]["id"] == "audit-legacy"
    with sqlite3.connect(tmp_path / "org.db") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == ORG_SCHEMA_VERSION
    events.close()
    audits.close()

    repeated = migrate_org_pilot_data(tmp_path, dry_run=False)
    assert repeated["executed"] is False
    assert repeated["candidates"] == {"workEvents": 0, "accessAudits": 0}

    rollback = rollback_org_pilot_data(result["manifestPath"])
    assert rollback["restored"] is True
    assert (tmp_path / "work_events.db").exists()
    assert (tmp_path / "access_audit.db").exists()

    restored_events = WorkEventStore(tmp_path)
    assert restored_events.get_work_event("event-legacy") is None
    restored_events.close()


def test_app_context_imports_legacy_pilot_before_opening_org_services(tmp_path):
    _seed_target_identity(tmp_path)
    _create_legacy_databases(tmp_path)

    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
    )

    assert context.org_event_store.get_work_event("event-legacy") is not None
    assert context.org_access_audit_store.list_audit_records("pilot-org")
    context.close()
