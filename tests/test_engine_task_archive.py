"""Tests for engine.task_archive — cold storage for old tasks."""

import pytest
from datetime import datetime, timezone, timedelta
from engine.task_archive import TaskArchiver
from engine.store import StoreManager
from engine.models import Task, TaskStatus


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path / "db")
    store.connect()
    archiver = TaskArchiver(store, tmp_path / "archive")
    yield archiver, store
    store.close()


class TestTaskArchiver:
    def test_archive_old(self, components):
        archiver, store = components
        # Create an old completed task
        t = Task(agent_id="claude", title="old task")
        t.created_at = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        store.insert_task(t)
        store.update_task_status(t.id, TaskStatus.COMPLETED)
        # Force old created_at
        store._tasks_db.execute(
            "UPDATE task SET created_at = ? WHERE id = ?",
            (t.created_at, t.id),
        )
        store._tasks_db.commit()

        count = archiver.archive_old(days=30)
        assert count == 1
        # Task should be gone from active DB
        assert store.get_task(t.id) is None

    def test_no_archive_recent(self, components):
        archiver, store = components
        t = Task(agent_id="claude", title="recent")
        store.insert_task(t)
        store.update_task_status(t.id, TaskStatus.COMPLETED)
        count = archiver.archive_old(days=30)
        assert count == 0

    def test_no_archive_running(self, components):
        archiver, store = components
        t = Task(agent_id="claude", title="running")
        t.created_at = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        store.insert_task(t)
        store._tasks_db.execute(
            "UPDATE task SET created_at = ?, status = 'running' WHERE id = ?",
            (t.created_at, t.id),
        )
        store._tasks_db.commit()
        count = archiver.archive_old(days=30)
        assert count == 0  # running tasks not archived

    def test_list_archives(self, components):
        archiver, store = components
        # Create and archive a task
        t = Task(agent_id="claude", title="archive me")
        t.created_at = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        store.insert_task(t)
        store.update_task_status(t.id, TaskStatus.COMPLETED)
        store._tasks_db.execute(
            "UPDATE task SET created_at = ? WHERE id = ?",
            (t.created_at, t.id),
        )
        store._tasks_db.commit()
        archiver.archive_old(days=30)

        archives = archiver.list_archives()
        assert len(archives) >= 1
        assert archives[0]["tasks"] >= 1

    def test_search_archive(self, components):
        archiver, store = components
        t = Task(agent_id="claude", title="searchable auth fix")
        t.created_at = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        store.insert_task(t)
        store.update_task_status(t.id, TaskStatus.COMPLETED)
        store._tasks_db.execute(
            "UPDATE task SET created_at = ? WHERE id = ?",
            (t.created_at, t.id),
        )
        store._tasks_db.commit()
        archiver.archive_old(days=30)

        results = archiver.search_archive("auth")
        assert len(results) >= 1
        assert "auth" in results[0]["title"]

    def test_empty_archive(self, components):
        archiver, _ = components
        assert archiver.list_archives() == []
        assert archiver.search_archive("anything") == []
