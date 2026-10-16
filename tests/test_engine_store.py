"""Tests for engine.store — three-database storage manager."""

import sqlite3
import threading
import time

import pytest
import tempfile
from pathlib import Path

from engine.store import ACTIVITY_SCHEMA_VERSION, TASKS_SCHEMA_VERSION, StoreManager
from engine.models import Task, TaskStatus, TaskMessage, ActivityEntry


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        s = StoreManager(Path(td))
        s.connect()
        yield s
        s.close()


class TestTaskCRUD:
    def test_insert_and_get(self, store):
        t = Task(
            agent_id="claude",
            title="write tests",
            source_agent="osborn",
            requester_user_id="user-123",
            workspace_id="workspace-456",
        )
        store.insert_task(t)
        row = store.get_task(t.id)
        assert row is not None
        assert row["title"] == "write tests"
        assert row["status"] == "draft"
        assert row["source_agent"] == "osborn"
        assert row["requester_user_id"] == "user-123"
        assert row["workspace_id"] == "workspace-456"

    def test_get_nonexistent(self, store):
        assert store.get_task("nope") is None

    def test_update_status(self, store):
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        store.update_task_status(t.id, TaskStatus.PLANNED)
        row = store.get_task(t.id)
        assert row["status"] == "planned"

    def test_update_with_kwargs(self, store):
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        store.update_task_status(t.id, TaskStatus.FAILED, error="timeout")
        row = store.get_task(t.id)
        assert row["status"] == "failed"
        assert row["error"] == "timeout"

    def test_list_tasks_all(self, store):
        for i in range(3):
            store.insert_task(Task(agent_id="claude", title=f"task-{i}"))
        assert len(store.list_tasks()) == 3

    def test_list_tasks_by_agent(self, store):
        store.insert_task(Task(agent_id="claude", title="a"))
        store.insert_task(Task(agent_id="codex", title="b"))
        assert len(store.list_tasks(agent_id="claude")) == 1

    def test_list_tasks_by_status(self, store):
        t = Task(agent_id="claude", title="a")
        store.insert_task(t)
        store.update_task_status(t.id, TaskStatus.PLANNED)
        store.insert_task(Task(agent_id="claude", title="b"))
        assert len(store.list_tasks(status="planned")) == 1
        assert len(store.list_tasks(status="draft")) == 1


class TestTaskMessageCRUD:
    def test_insert_and_list(self, store):
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)

        for i in range(5):
            store.insert_task_message(TaskMessage(
                task_id=t.id, seq=i + 1,
                author_type="agent", author_id="claude",
                type="text", content=f"msg-{i}",
            ))

        msgs = store.list_task_messages(t.id)
        assert len(msgs) == 5
        assert msgs[0]["seq"] == 1
        assert msgs[4]["content"] == "msg-4"

    def test_messages_ordered_by_seq(self, store):
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        # insert out of order
        store.insert_task_message(TaskMessage(
            task_id=t.id, seq=3, author_type="agent", author_id="claude",
            type="text", content="third",
        ))
        store.insert_task_message(TaskMessage(
            task_id=t.id, seq=1, author_type="agent", author_id="claude",
            type="text", content="first",
        ))
        msgs = store.list_task_messages(t.id)
        assert msgs[0]["content"] == "first"
        assert msgs[1]["content"] == "third"

    def test_empty_messages(self, store):
        assert store.list_task_messages("nonexistent") == []


class TestActivityLog:
    def test_insert(self, store):
        entry = ActivityEntry(
            actor_type="system", actor_id="scheduler",
            action="task:created",
            target_type="task", target_id="t1",
            details={"title": "test"},
        )
        store.insert_activity(entry)
        # no crash = pass (no list method yet, just verify write)


class TestDatabaseIsolation:
    def test_separate_files(self, store):
        """tasks.db and activity.db should be separate files."""
        files = list(store.data_dir.glob("*.db"))
        db_names = {f.name for f in files}
        assert "tasks.db" in db_names
        assert "activity.db" in db_names

    def test_wal_mode(self, store):
        """Both databases should use WAL journal mode."""
        mode = store._tasks_db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        mode = store._activity_db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_windows_safe_connection_pragmas(self, store):
        """Every engine database uses bounded lock waits and durable WAL settings."""
        for connection in (store._tasks_db, store._activity_db):
            assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_second_writer_waits_for_lock_and_commits(self, tmp_path):
        initial = StoreManager(tmp_path)
        initial.connect()
        initial.close()

        holder = sqlite3.connect(tmp_path / "tasks.db", timeout=5)
        holder.execute("PRAGMA busy_timeout=5000")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute(
            """INSERT INTO task (id, agent_id, title, created_at)
               VALUES ('holder', 'probe', 'holder', '2026-07-19T00:00:00Z')"""
        )

        outcome: dict[str, object] = {}

        def write_contender() -> None:
            contender = StoreManager(tmp_path)
            started = time.monotonic()
            try:
                contender.connect()
                contender.insert_task(
                    Task(id="contender", agent_id="probe", title="contender")
                )
                outcome["elapsed"] = time.monotonic() - started
            except Exception as exc:  # pragma: no cover - asserted below
                outcome["error"] = exc
            finally:
                contender.close()

        thread = threading.Thread(target=write_contender)
        thread.start()
        time.sleep(0.15)
        holder.commit()
        holder.close()
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert "error" not in outcome
        assert float(outcome["elapsed"]) >= 0.1

        check = sqlite3.connect(tmp_path / "tasks.db")
        ids = {row[0] for row in check.execute("SELECT id FROM task")}
        check.close()
        assert ids == {"holder", "contender"}


def test_default_store_directory_uses_vox_agent_namespace(monkeypatch, tmp_path):
    canonical = tmp_path / "Vox Agent"
    monkeypatch.setenv("VOXAGENT_DATA_DIR", str(canonical))

    store = StoreManager()
    try:
        assert store.data_dir == canonical
        assert "VoxLog" not in str(store.data_dir)
    finally:
        store.close()


class TestSchemaMigrations:
    def test_connect_owns_the_complete_tasks_schema(self, tmp_path):
        store = StoreManager(tmp_path)
        store.connect()

        tables = {
            row[0]
            for row in store._tasks_db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        columns = {
            row[1] for row in store._tasks_db.execute("PRAGMA table_info(task)")
        }

        assert {
            "task",
            "task_message",
            "task_annotation",
            "task_comment",
            "task_dependency",
            "task_history",
        } <= tables
        assert {
            "favorite",
            "tags",
            "source_agent",
            "requester_user_id",
            "workspace_id",
        } <= columns
        assert store._tasks_db.execute("PRAGMA user_version").fetchone()[0] == TASKS_SCHEMA_VERSION
        assert (
            store._activity_db.execute("PRAGMA user_version").fetchone()[0]
            == ACTIVITY_SCHEMA_VERSION
        )
        store.close()

    def test_connect_migrates_legacy_tasks_without_losing_rows(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(tmp_path / "tasks.db")
        db.executescript("""
            CREATE TABLE task (
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
            INSERT INTO task (id, agent_id, title, created_at)
            VALUES ('legacy', 'claude', 'keep me', '2026-07-17T00:00:00Z');
            PRAGMA user_version = 1;
        """)
        db.close()

        store = StoreManager(tmp_path)
        store.connect()

        row = store.get_task("legacy")
        assert row is not None
        assert row["title"] == "keep me"
        assert row["favorite"] == 0
        assert row["tags"] == ""
        assert row["source_agent"] is None
        assert row["requester_user_id"] == ""
        assert row["workspace_id"] == ""
        assert store._tasks_db.execute("PRAGMA user_version").fetchone()[0] == TASKS_SCHEMA_VERSION
        store.close()

    def test_close_allows_clean_reconnect(self, tmp_path):
        store = StoreManager(tmp_path)
        store.connect()
        store.close()

        store.connect()
        assert store.list_tasks() == []
        store.close()
