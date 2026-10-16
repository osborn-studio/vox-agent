"""Tests for engine.analytics — task execution statistics."""

import pytest

from engine.analytics import TaskAnalytics
from engine.store import StoreManager
from engine.models import Task, TaskStatus


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    analytics = TaskAnalytics(store)
    yield analytics, store
    store.close()


class TestEngineStats:
    def test_empty(self, components):
        analytics, _ = components
        stats = analytics.engine_stats()
        assert stats.total_tasks == 0
        assert stats.agents == []

    def test_with_tasks(self, components):
        analytics, store = components
        store.insert_task(Task(agent_id="claude", title="a", status=TaskStatus.COMPLETED))
        store.insert_task(Task(agent_id="claude", title="b", status=TaskStatus.FAILED))
        store.insert_task(Task(agent_id="codex", title="c", status=TaskStatus.COMPLETED))

        stats = analytics.engine_stats()
        assert stats.total_tasks == 3
        assert stats.completed == 2
        assert stats.failed == 1
        assert len(stats.agents) == 2

    def test_tasks_today(self, components):
        analytics, store = components
        store.insert_task(Task(agent_id="claude", title="today"))
        stats = analytics.engine_stats()
        assert stats.tasks_today == 1
        assert stats.tasks_this_week == 1


class TestAgentStats:
    def test_single_agent(self, components):
        analytics, store = components
        for i in range(5):
            t = Task(agent_id="claude", title=f"task-{i}")
            store.insert_task(t)
            if i < 3:
                store.update_task_status(t.id, TaskStatus.COMPLETED)
            elif i == 3:
                store.update_task_status(t.id, TaskStatus.FAILED)

        stats = analytics.agent_stats("claude")
        assert stats.total_tasks == 5
        assert stats.completed == 3
        assert stats.failed == 1
        assert stats.success_rate == 0.75  # 3/(3+1)

    def test_no_tasks(self, components):
        analytics, _ = components
        stats = analytics.agent_stats("nonexistent")
        assert stats.total_tasks == 0
        assert stats.success_rate == 0.0

    def test_duration_calculation(self, components):
        analytics, store = components
        t = Task(agent_id="claude", title="timed")
        store.insert_task(t)
        store.update_task_status(
            t.id, TaskStatus.COMPLETED,
            started_at="2026-04-06T10:00:00+00:00",
            completed_at="2026-04-06T10:00:30+00:00",
        )
        stats = analytics.agent_stats("claude")
        assert stats.avg_duration_sec == pytest.approx(30.0, abs=1)
