"""Three-database storage manager (messages.db / tasks.db / activity.db)."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from runtime.data_paths import resolve_vox_agent_data_dir
from runtime.sqlite_policy import configure_sqlite_connection

from .models import Task, TaskMessage, ActivityEntry, TaskStatus

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = resolve_vox_agent_data_dir()
TASKS_SCHEMA_VERSION = 5
ACTIVITY_SCHEMA_VERSION = 1


class StoreManager:
    """Manages three isolated SQLite databases.

    messages.db  — voice records (high-freq append, FTS5, Obsidian sync)
    tasks.db     — tasks + execution messages (stateful, range indexes)
    activity.db  — audit log (low-freq, analysis)
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or resolve_vox_agent_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._tasks_db: sqlite3.Connection | None = None
        self._activity_db: sqlite3.Connection | None = None

    def connect(self) -> None:
        if self._tasks_db is not None or self._activity_db is not None:
            self.close()
        self._tasks_db = self._open(self.data_dir / "tasks.db")
        self._activity_db = self._open(self.data_dir / "activity.db")
        self._migrate_tasks()
        self._migrate_activity()

    def close(self) -> None:
        for db in (self._tasks_db, self._activity_db):
            if db:
                db.close()
        self._tasks_db = None
        self._activity_db = None

    # -- tasks.db ------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._tasks_db is None:
            self.connect()

    def insert_task(self, task: Task) -> None:
        self._ensure_connected()
        self._tasks_db.execute(
            """INSERT INTO task
               (id, agent_id, trigger_message_id, source_agent, status, priority,
                title, description, prompt, requester_user_id, workspace_id,
                created_at, working_directory)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.id, task.agent_id, task.trigger_message_id, task.source_agent,
                task.status.value, task.priority.value,
                task.title, task.description, task.prompt,
                task.requester_user_id, task.workspace_id, task.created_at, task.working_directory,
            ),
        )
        self._tasks_db.commit()

    def update_task_status(
        self, task_id: str, status: TaskStatus, **kwargs: Any
    ) -> None:
        self._ensure_connected()
        sets = ["status = ?"]
        vals: list[Any] = [status.value]
        for k, v in kwargs.items():
            if v is not None:
                sets.append(f"{k} = ?")
                vals.append(v if not isinstance(v, dict) else json.dumps(v))
        vals.append(task_id)
        self._tasks_db.execute(
            f"UPDATE task SET {', '.join(sets)} WHERE id = ?", vals
        )
        self._tasks_db.commit()

    def get_task(self, task_id: str) -> dict | None:
        self._ensure_connected()
        row = self._tasks_db.execute(
            "SELECT * FROM task WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_tasks(self, agent_id: str | None = None, status: str | None = None) -> list[dict]:
        self._ensure_connected()
        sql = "SELECT * FROM task WHERE 1=1"
        params: list[Any] = []
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        return [dict(r) for r in self._tasks_db.execute(sql, params).fetchall()]

    def insert_task_message(self, msg: TaskMessage) -> None:
        self._ensure_connected()
        self._tasks_db.execute(
            """INSERT INTO task_message
               (id, task_id, seq, author_type, author_id, type,
                content, tool, input, output, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg.id, msg.task_id, msg.seq, msg.author_type, msg.author_id,
                msg.type, msg.content, msg.tool,
                json.dumps(msg.input) if msg.input else None,
                msg.output, msg.created_at,
            ),
        )
        self._tasks_db.commit()

    def list_task_messages(self, task_id: str) -> list[dict]:
        self._ensure_connected()
        return [
            dict(r)
            for r in self._tasks_db.execute(
                "SELECT * FROM task_message WHERE task_id = ? ORDER BY seq",
                (task_id,),
            ).fetchall()
        ]

    # -- activity.db ---------------------------------------------------------

    def insert_activity(self, entry: ActivityEntry) -> None:
        self._ensure_connected()
        self._activity_db.execute(
            """INSERT INTO activity_log
               (id, actor_type, actor_id, action, target_type, target_id,
                details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id, entry.actor_type, entry.actor_id, entry.action,
                entry.target_type, entry.target_id,
                json.dumps(entry.details) if entry.details else None,
                entry.created_at,
            ),
        )
        self._activity_db.commit()

    # -- internal ------------------------------------------------------------

    @staticmethod
    def _open(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(path), timeout=5)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn)
        return conn

    def _migrate_tasks(self) -> None:
        # called from connect(), do not call _ensure_connected here
        current = self._tasks_db.execute("PRAGMA user_version").fetchone()[0]
        if current > TASKS_SCHEMA_VERSION:
            raise RuntimeError(
                f"tasks.db schema {current} is newer than supported {TASKS_SCHEMA_VERSION}"
            )
        migrations = {
            1: self._migrate_tasks_v1,
            2: self._migrate_tasks_v2,
            3: self._migrate_tasks_v3,
            4: self._migrate_tasks_v4,
            5: self._migrate_tasks_v5,
        }
        for version in range(current + 1, TASKS_SCHEMA_VERSION + 1):
            migrations[version]()
            self._tasks_db.execute(f"PRAGMA user_version = {version}")
            self._tasks_db.commit()

    def _migrate_tasks_v1(self) -> None:
        self._tasks_db.executescript("""
            CREATE TABLE IF NOT EXISTS task (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                trigger_message_id TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                priority INTEGER DEFAULT 0,
                title TEXT NOT NULL,
                description TEXT,
                prompt TEXT,
                result TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_task_agent ON task(agent_id, status);
            CREATE INDEX IF NOT EXISTS idx_task_status ON task(status, created_at);

            CREATE TABLE IF NOT EXISTS task_message (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES task(id),
                seq INTEGER NOT NULL,
                author_type TEXT NOT NULL,
                author_id TEXT NOT NULL,
                type TEXT NOT NULL,
                content TEXT,
                tool TEXT,
                input TEXT,
                output TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_task_message_task
                ON task_message(task_id, seq);
        """)

    def _migrate_tasks_v2(self) -> None:
        columns = {
            row[1] for row in self._tasks_db.execute("PRAGMA table_info(task)")
        }
        if "favorite" not in columns:
            self._tasks_db.execute(
                "ALTER TABLE task ADD COLUMN favorite INTEGER DEFAULT 0"
            )
        if "tags" not in columns:
            self._tasks_db.execute(
                "ALTER TABLE task ADD COLUMN tags TEXT DEFAULT ''"
            )
        self._tasks_db.executescript("""
            CREATE TABLE IF NOT EXISTS task_annotation (
                task_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (task_id, key)
            );

            CREATE TABLE IF NOT EXISTS task_comment (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                author_type TEXT NOT NULL,
                author_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_comment_task
                ON task_comment(task_id, created_at);

            CREATE TABLE IF NOT EXISTS task_dependency (
                task_id TEXT NOT NULL,
                depends_on TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY (task_id, depends_on)
            );

            CREATE TABLE IF NOT EXISTS task_history (
                task_id TEXT NOT NULL,
                from_status TEXT NOT NULL,
                to_status TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                actor TEXT DEFAULT 'system',
                reason TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_history_task
                ON task_history(task_id, timestamp);
        """)

    def _migrate_tasks_v3(self) -> None:
        columns = {
            row[1] for row in self._tasks_db.execute("PRAGMA table_info(task)")
        }
        if "source_agent" not in columns:
            self._tasks_db.execute("ALTER TABLE task ADD COLUMN source_agent TEXT")

    def _migrate_tasks_v4(self) -> None:
        columns = {
            row[1] for row in self._tasks_db.execute("PRAGMA table_info(task)")
        }
        if "requester_user_id" not in columns:
            self._tasks_db.execute(
                "ALTER TABLE task ADD COLUMN requester_user_id TEXT NOT NULL DEFAULT ''"
            )
        if "workspace_id" not in columns:
            self._tasks_db.execute(
                "ALTER TABLE task ADD COLUMN workspace_id TEXT NOT NULL DEFAULT ''"
            )

    def _migrate_tasks_v5(self) -> None:
        self._tasks_db.execute("ALTER TABLE task ADD COLUMN working_directory TEXT")

    def _migrate_activity(self) -> None:
        # called from connect(), do not call _ensure_connected here
        current = self._activity_db.execute("PRAGMA user_version").fetchone()[0]
        if current > ACTIVITY_SCHEMA_VERSION:
            raise RuntimeError(
                "activity.db schema "
                f"{current} is newer than supported {ACTIVITY_SCHEMA_VERSION}"
            )
        if current == ACTIVITY_SCHEMA_VERSION:
            return
        self._activity_db.executescript("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id TEXT PRIMARY KEY,
                actor_type TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_activity_actor
                ON activity_log(actor_type, actor_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_activity_target
                ON activity_log(target_type, target_id);
        """)
        self._activity_db.execute(f"PRAGMA user_version = {ACTIVITY_SCHEMA_VERSION}")
        self._activity_db.commit()
