"""SQLite-backed voice memory repository.

Same duck-typed surface as engine.voice_memory.VoiceMemoryRepository —
endpoints in engine/server.py call either implementation interchangeably
(selection via VOXAGENT_VOICE_MEMORY_DB, see server bootstrap).

Rows store the full camelCase dict as a JSON blob plus a few indexed
columns; list/search semantics reuse the shared helpers from
engine.voice_memory so both implementations stay behavior-identical.
Follows engine/store.py conventions: stdlib sqlite3, WAL, no ORM.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.sqlite_policy import configure_sqlite_connection

from .voice_memory import (
    _SESSION_PATCHABLE_FIELDS,
    build_node,
    build_segment,
    build_session,
    node_in_scope,
    now_iso,
    rank_nodes,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transcript_segments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_session ON transcript_segments(session_id, seq);
CREATE TABLE IF NOT EXISTS memory_nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    source_session_id TEXT,
    created_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_session ON memory_nodes(source_session_id);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON memory_nodes(node_type);
CREATE TABLE IF NOT EXISTS session_audio (
    session_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    created_at TEXT
);
"""

class SqliteVoiceMemoryRepository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        configure_sqlite_connection(self._db)
        self._db.executescript(_SCHEMA)
        self._migrate_retention_schema()
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # -- Capture sessions -------------------------------------------------

    def create_session(self, payload: dict) -> dict:
        session = build_session(payload)
        self._db.execute(
            "INSERT INTO capture_sessions (id, created_at, data) VALUES (?, ?, ?)",
            (session["id"], session["createdAt"], json.dumps(session)),
        )
        self._db.commit()
        return session

    def get_session(self, session_id: str) -> dict | None:
        row = self._db.execute(
            "SELECT data FROM capture_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def list_sessions(
        self,
        limit: int = 50,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list:
        rows = self._db.execute(
            "SELECT data FROM capture_sessions ORDER BY created_at DESC",
        ).fetchall()
        sessions = []
        for row in rows:
            session = json.loads(row[0])
            if (
                workspace_id is not None
                and session.get("workspaceId") != workspace_id
            ):
                continue
            if (
                owner_user_id is not None
                and session.get("ownerUserId") != owner_user_id
            ):
                continue
            sessions.append(session)
            if len(sessions) >= limit:
                break
        return sessions

    def update_session(self, session_id: str, payload: dict) -> dict:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        for key in _SESSION_PATCHABLE_FIELDS:
            if key in payload:
                session[key] = payload[key]
        session["updatedAt"] = now_iso()
        self._db.execute(
            "UPDATE capture_sessions SET data = ? WHERE id = ?",
            (json.dumps(session), session_id),
        )
        self._db.commit()
        return session

    # -- Transcript segments ----------------------------------------------

    def add_segment(self, session_id: str, payload: dict) -> dict:
        if self.get_session(session_id) is None:
            raise KeyError(session_id)
        segment = build_segment(session_id, payload)
        (next_seq,) = self._db.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM transcript_segments WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        self._db.execute(
            "INSERT INTO transcript_segments (id, session_id, seq, data) VALUES (?, ?, ?, ?)",
            (segment["id"], session_id, next_seq, json.dumps(segment)),
        )
        self._db.commit()
        return segment

    def list_segments(self, session_id: str) -> list:
        if self.get_session(session_id) is None:
            raise KeyError(session_id)
        rows = self._db.execute(
            "SELECT data FROM transcript_segments WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    # -- Audio path store ---------------------------------------------------

    def set_audio_path(self, session_id: str, path: str) -> None:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        self._db.execute(
            """INSERT OR REPLACE INTO session_audio (session_id, path, created_at)
               VALUES (?, ?, ?)""",
            (session_id, path, session["createdAt"]),
        )
        self._db.commit()

    def get_audio_path(self, session_id: str) -> str | None:
        row = self._db.execute(
            "SELECT path FROM session_audio WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else None

    # -- Memory nodes -------------------------------------------------------

    def add_memory_node(self, payload: dict) -> dict:
        node = build_node(payload)
        self._db.execute(
            "INSERT INTO memory_nodes (id, node_type, source_session_id, created_at, data)"
            " VALUES (?, ?, ?, ?, ?)",
            (node["id"], node["type"], node.get("sourceSessionId"),
             node["createdAt"], json.dumps(node)),
        )
        self._db.commit()
        return node

    def get_node(
        self,
        node_id: str,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> dict | None:
        row = self._db.execute(
            "SELECT data FROM memory_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        node = json.loads(row[0])
        if not node_in_scope(
            node,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
        ):
            return None
        return node

    def list_recent_nodes(
        self,
        days: int = 7,
        limit: int = 20,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._db.execute(
            "SELECT data FROM memory_nodes WHERE created_at >= ?"
            " ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        nodes = [
            node
            for row in rows
            if node_in_scope(
                (node := json.loads(row[0])),
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
            )
        ]
        return nodes[:limit]

    def list_action_items(
        self,
        status: str | None = None,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list:
        rows = self._db.execute(
            "SELECT data FROM memory_nodes WHERE node_type = 'ACTION_ITEM'"
        ).fetchall()
        items = [
            node
            for row in rows
            if node_in_scope(
                (node := json.loads(row[0])),
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
            )
        ]
        if status is not None:
            items = [n for n in items if n.get("metadata", {}).get("status") == status]
        return items

    def list_nodes_for_session(
        self,
        session_id: str,
        node_type: str | None = None,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list:
        if node_type is None:
            rows = self._db.execute(
                "SELECT data FROM memory_nodes WHERE source_session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT data FROM memory_nodes WHERE source_session_id = ? AND node_type = ?"
                " ORDER BY created_at",
                (session_id, node_type),
            ).fetchall()
        return [
            node
            for row in rows
            if node_in_scope(
                (node := json.loads(row[0])),
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
            )
        ]

    def search_memory(
        self,
        query: str,
        limit: int = 20,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> dict:
        rows = self._db.execute("SELECT data FROM memory_nodes").fetchall()
        nodes = [
            node
            for row in rows
            if node_in_scope(
                (node := json.loads(row[0])),
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
            )
        ]
        return {"query": query, "results": rank_nodes(nodes, query, limit)}

    # -- Retention ---------------------------------------------------------

    def retention_candidates(
        self,
        *,
        audio_before: str,
        transcript_before: str,
        memory_before: str,
    ) -> dict:
        audio = [
            {"sessionId": row["session_id"], "path": row["path"]}
            for row in self._db.execute(
                """SELECT session_id, path FROM session_audio
                   WHERE created_at < ? ORDER BY created_at, session_id""",
                (audio_before,),
            ).fetchall()
        ]
        transcripts = [
            row["session_id"]
            for row in self._db.execute(
                """SELECT DISTINCT t.session_id
                   FROM transcript_segments AS t
                   JOIN capture_sessions AS s ON s.id = t.session_id
                   WHERE s.created_at < ? ORDER BY t.session_id""",
                (transcript_before,),
            ).fetchall()
        ]
        memory = [
            row["id"]
            for row in self._db.execute(
                "SELECT id FROM memory_nodes WHERE created_at < ? ORDER BY created_at, id",
                (memory_before,),
            ).fetchall()
        ]
        return {"audio": audio, "transcripts": transcripts, "memory": memory}

    def delete_retention_candidates(
        self,
        *,
        audio_session_ids: list[str],
        transcript_session_ids: list[str],
        memory_node_ids: list[str],
    ) -> dict:
        with self._db:
            audio_deleted = self._delete_ids("session_audio", "session_id", audio_session_ids)
            transcript_deleted = self._delete_ids(
                "transcript_segments", "session_id", transcript_session_ids
            )
            memory_deleted = self._delete_ids("memory_nodes", "id", memory_node_ids)
        return {
            "audio": audio_deleted,
            "transcripts": transcript_deleted,
            "memory": memory_deleted,
        }

    def _delete_ids(self, table: str, column: str, values: list[str]) -> int:
        if not values:
            return 0
        placeholders = ",".join("?" for _ in values)
        cursor = self._db.execute(
            f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
            values,
        )
        return cursor.rowcount

    def _migrate_retention_schema(self) -> None:
        columns = {
            row["name"] for row in self._db.execute("PRAGMA table_info(session_audio)").fetchall()
        }
        if "created_at" not in columns:
            self._db.execute("ALTER TABLE session_audio ADD COLUMN created_at TEXT")
        self._db.execute(
            """UPDATE session_audio
               SET created_at = COALESCE(
                   created_at,
                   (SELECT created_at FROM capture_sessions WHERE id = session_audio.session_id),
                   CURRENT_TIMESTAMP
               )
               WHERE created_at IS NULL"""
        )
