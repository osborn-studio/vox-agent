"""Versioned schema authority for organization collaboration data."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from runtime.sqlite_policy import configure_sqlite_connection

ORG_DATABASE_NAME = "org.db"
ORG_SCHEMA_VERSION = 2

WORK_EVENT_COLUMNS = (
    "id",
    "org_id",
    "team_id",
    "project_id",
    "workspace_id",
    "actor_type",
    "actor_id",
    "source_type",
    "visibility",
    "retention_class",
    "body",
    "metadata",
    "redaction_reasons",
    "source_hash",
    "created_at",
)

ACCESS_AUDIT_COLUMNS = (
    "id",
    "org_id",
    "requester_user_id",
    "target_actor_id",
    "target_scope",
    "decision",
    "reason",
    "source_ids",
    "created_at",
)


def open_org_database(data_dir: Path) -> sqlite3.Connection:
    """Open the shared organization database with the product SQLite policy."""

    connection = sqlite3.connect(
        str(data_dir / ORG_DATABASE_NAME),
        timeout=5,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    configure_sqlite_connection(connection)
    return connection


def migrate_org_schema(connection: sqlite3.Connection) -> None:
    """Bring identity, event, and access-audit tables to the supported version."""

    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current > ORG_SCHEMA_VERSION:
        raise RuntimeError(
            f"org.db schema {current} is newer than supported {ORG_SCHEMA_VERSION}"
        )
    if current < 1:
        _migrate_v1(connection)
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        current = 1
    if current < 2:
        _migrate_v2(connection)
        connection.execute("PRAGMA user_version = 2")
        connection.commit()


def _migrate_v1(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS organization (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL REFERENCES organization(id),
            email TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            UNIQUE(org_id, email)
        );
        CREATE INDEX IF NOT EXISTS idx_user_org_role
            ON user(org_id, role, status);

        CREATE TABLE IF NOT EXISTS team (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL REFERENCES organization(id),
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(org_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_team_org ON team(org_id);

        CREATE TABLE IF NOT EXISTS team_member (
            team_id TEXT NOT NULL REFERENCES team(id),
            user_id TEXT NOT NULL REFERENCES user(id),
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL,
            PRIMARY KEY(team_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_team_member_user
            ON team_member(user_id, team_id);

        CREATE TABLE IF NOT EXISTS agent_identity (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL REFERENCES organization(id),
            provider TEXT NOT NULL,
            display_name TEXT NOT NULL,
            owner_user_id TEXT REFERENCES user(id),
            visibility_scope TEXT NOT NULL DEFAULT 'team',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_identity_org
            ON agent_identity(org_id, visibility_scope);

        CREATE TABLE IF NOT EXISTS api_credential (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES user(id),
            name TEXT NOT NULL,
            salt TEXT NOT NULL,
            secret_hash TEXT NOT NULL,
            last_used_at TEXT,
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            UNIQUE(user_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_api_credential_user_active
            ON api_credential(user_id, revoked_at);

        CREATE TABLE IF NOT EXISTS work_event (
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
        CREATE INDEX IF NOT EXISTS idx_work_event_org_created
            ON work_event(org_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_work_event_team_project
            ON work_event(team_id, project_id, visibility);
        CREATE INDEX IF NOT EXISTS idx_work_event_actor
            ON work_event(actor_type, actor_id, created_at);

        CREATE TABLE IF NOT EXISTS access_audit (
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
        CREATE INDEX IF NOT EXISTS idx_access_audit_org_created
            ON access_audit(org_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_access_audit_requester
            ON access_audit(requester_user_id, decision, created_at);
        """
    )


def _migrate_v2(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS project (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL REFERENCES organization(id),
            team_id TEXT REFERENCES team(id),
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(org_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_project_org_team
            ON project(org_id, team_id);

        CREATE TABLE IF NOT EXISTS project_member (
            project_id TEXT NOT NULL REFERENCES project(id),
            user_id TEXT NOT NULL REFERENCES user(id),
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL,
            PRIMARY KEY(project_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_project_member_user
            ON project_member(user_id, project_id);
        """
    )
