"""SQLite storage for organization collaboration identity data."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from runtime.data_paths import resolve_vox_agent_data_dir

from .org_models import (
    AgentIdentity,
    Organization,
    Project,
    ProjectMember,
    Team,
    TeamMember,
    User,
)
from .org_schema import migrate_org_schema, open_org_database


class OrgStore:
    """Manages organization, team, user, and agent identity records."""

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

    def insert_organization(self, org: Organization) -> None:
        self._ensure_connected()
        self._db.execute(
            """INSERT INTO organization (id, name, created_at)
               VALUES (?, ?, ?)""",
            (org.id, org.name, org.created_at),
        )
        self._db.commit()

    def get_organization(self, org_id: str) -> dict | None:
        self._ensure_connected()
        row = self._db.execute(
            "SELECT * FROM organization WHERE id = ?", (org_id,)
        ).fetchone()
        return dict(row) if row else None

    def insert_user(self, user: User) -> None:
        self._ensure_connected()
        self._db.execute(
            """INSERT INTO user
               (id, org_id, email, display_name, role, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user.id,
                user.org_id,
                user.email,
                user.display_name,
                user.role.value,
                user.status,
                user.created_at,
            ),
        )
        self._db.commit()

    def get_user(self, user_id: str) -> dict | None:
        self._ensure_connected()
        row = self._db.execute(
            "SELECT * FROM user WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, org_id: str, email: str) -> dict | None:
        self._ensure_connected()
        row = self._db.execute(
            "SELECT * FROM user WHERE org_id = ? AND email = ?",
            (org_id, email),
        ).fetchone()
        return dict(row) if row else None

    def list_users(self, org_id: str) -> list[dict]:
        self._ensure_connected()
        return [
            dict(row)
            for row in self._db.execute(
                """SELECT id, org_id, email, display_name, role, status, created_at
                   FROM user WHERE org_id = ?
                   ORDER BY display_name COLLATE NOCASE, email COLLATE NOCASE""",
                (org_id,),
            ).fetchall()
        ]

    def update_user(self, user_id: str, **changes: str) -> dict | None:
        self._ensure_connected()
        allowed = {"display_name", "role", "status"}
        selected = {key: value for key, value in changes.items() if key in allowed}
        if selected:
            assignments = ", ".join(f"{key} = ?" for key in selected)
            self._db.execute(
                f"UPDATE user SET {assignments} WHERE id = ?",
                (*selected.values(), user_id),
            )
            self._db.commit()
        return self.get_user(user_id)

    def insert_team(self, team: Team) -> None:
        self._ensure_connected()
        self._db.execute(
            """INSERT INTO team (id, org_id, name, created_at)
               VALUES (?, ?, ?, ?)""",
            (team.id, team.org_id, team.name, team.created_at),
        )
        self._db.commit()

    def get_team(self, team_id: str) -> dict | None:
        self._ensure_connected()
        row = self._db.execute(
            "SELECT * FROM team WHERE id = ?", (team_id,)
        ).fetchone()
        return dict(row) if row else None

    def insert_team_member(self, member: TeamMember) -> None:
        self._ensure_connected()
        self._db.execute(
            """INSERT INTO team_member (team_id, user_id, role, created_at)
               VALUES (?, ?, ?, ?)""",
            (member.team_id, member.user_id, member.role.value, member.created_at),
        )
        self._db.commit()

    def list_team_members(self, team_id: str) -> list[dict]:
        self._ensure_connected()
        return [
            dict(row)
            for row in self._db.execute(
                "SELECT * FROM team_member WHERE team_id = ? ORDER BY created_at",
                (team_id,),
            ).fetchall()
        ]

    def get_team_role(self, user_id: str, team_id: str) -> str | None:
        self._ensure_connected()
        row = self._db.execute(
            """SELECT role FROM team_member
               WHERE user_id = ? AND team_id = ?""",
            (user_id, team_id),
        ).fetchone()
        return row["role"] if row else None

    def resolve_user_role(self, user_id: str, team_id: str | None = None) -> str | None:
        self._ensure_connected()
        if team_id:
            team_role = self.get_team_role(user_id, team_id)
            if team_role:
                return team_role

        user = self.get_user(user_id)
        return user["role"] if user else None

    def insert_project(self, project: Project) -> None:
        self._ensure_connected()
        self._db.execute(
            """INSERT INTO project (id, org_id, team_id, name, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                project.id,
                project.org_id,
                project.team_id,
                project.name,
                project.created_at,
            ),
        )
        self._db.commit()

    def get_project(self, project_id: str) -> dict | None:
        self._ensure_connected()
        row = self._db.execute(
            "SELECT * FROM project WHERE id = ?",
            (project_id,),
        ).fetchone()
        return dict(row) if row else None

    def insert_project_member(self, member: ProjectMember) -> None:
        self._ensure_connected()
        self._db.execute(
            """INSERT INTO project_member (project_id, user_id, role, created_at)
               VALUES (?, ?, ?, ?)""",
            (
                member.project_id,
                member.user_id,
                member.role.value,
                member.created_at,
            ),
        )
        self._db.commit()

    def get_project_role(self, user_id: str, project_id: str) -> str | None:
        self._ensure_connected()
        row = self._db.execute(
            """SELECT role FROM project_member
               WHERE user_id = ? AND project_id = ?""",
            (user_id, project_id),
        ).fetchone()
        return row["role"] if row else None

    def insert_agent_identity(self, agent: AgentIdentity) -> None:
        self._ensure_connected()
        self._db.execute(
            """INSERT INTO agent_identity
               (id, org_id, provider, display_name, owner_user_id,
                visibility_scope, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                agent.id,
                agent.org_id,
                agent.provider,
                agent.display_name,
                agent.owner_user_id,
                agent.visibility_scope.value,
                agent.created_at,
            ),
        )
        self._db.commit()

    def get_agent_identity(self, agent_id: str) -> dict | None:
        self._ensure_connected()
        row = self._db.execute(
            "SELECT * FROM agent_identity WHERE id = ?", (agent_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_agents(self, org_id: str | None = None) -> list[dict]:
        self._ensure_connected()
        sql = "SELECT * FROM agent_identity"
        params: list[Any] = []
        if org_id:
            sql += " WHERE org_id = ?"
            params.append(org_id)
        sql += " ORDER BY created_at"
        return [dict(row) for row in self._db.execute(sql, params).fetchall()]

    def upsert_api_credential(
        self,
        *,
        credential_id: str,
        user_id: str,
        name: str,
        salt: str,
        secret_hash: str,
        created_at: str,
    ) -> None:
        self._ensure_connected()
        self._db.execute(
            """INSERT INTO api_credential
               (id, user_id, name, salt, secret_hash, created_at, revoked_at)
               VALUES (?, ?, ?, ?, ?, ?, NULL)
               ON CONFLICT(user_id, name) DO UPDATE SET
                   salt = excluded.salt,
                   secret_hash = excluded.secret_hash,
                   created_at = excluded.created_at,
                   revoked_at = NULL""",
            (credential_id, user_id, name, salt, secret_hash, created_at),
        )
        self._db.commit()

    def list_active_auth_candidates(self) -> list[dict]:
        self._ensure_connected()
        rows = self._db.execute(
            """SELECT c.id AS credential_id, c.user_id, c.salt, c.secret_hash,
                      u.org_id, u.role, u.status
               FROM api_credential AS c
               JOIN user AS u ON u.id = c.user_id
               WHERE c.revoked_at IS NULL"""
        ).fetchall()
        return [dict(row) for row in rows]

    def count_active_credentials(self) -> int:
        self._ensure_connected()
        row = self._db.execute(
            "SELECT COUNT(*) AS count FROM api_credential WHERE revoked_at IS NULL"
        ).fetchone()
        return int(row["count"])

    def touch_api_credential(self, credential_id: str, last_used_at: str) -> None:
        self._ensure_connected()
        self._db.execute(
            "UPDATE api_credential SET last_used_at = ? WHERE id = ?",
            (last_used_at, credential_id),
        )
        self._db.commit()

    def get_api_credential(self, credential_id: str) -> dict | None:
        self._ensure_connected()
        row = self._db.execute(
            """SELECT c.id, c.user_id, c.name, c.created_at, c.revoked_at, u.org_id
               FROM api_credential AS c
               JOIN user AS u ON u.id = c.user_id
               WHERE c.id = ?""",
            (credential_id,),
        ).fetchone()
        return dict(row) if row else None

    def revoke_api_credential(
        self,
        credential_id: str,
        revoked_at: str,
        *,
        org_id: str | None = None,
    ) -> bool:
        self._ensure_connected()
        if org_id is None:
            cursor = self._db.execute(
                """UPDATE api_credential SET revoked_at = ?
                   WHERE id = ? AND revoked_at IS NULL""",
                (revoked_at, credential_id),
            )
        else:
            cursor = self._db.execute(
                """UPDATE api_credential SET revoked_at = ?
                   WHERE id = ? AND revoked_at IS NULL
                     AND user_id IN (SELECT id FROM user WHERE org_id = ?)""",
                (revoked_at, credential_id, org_id),
            )
        self._db.commit()
        return cursor.rowcount > 0

    def _ensure_connected(self) -> None:
        if self._db is None:
            self.connect()

    def _migrate(self) -> None:
        migrate_org_schema(self._db)
