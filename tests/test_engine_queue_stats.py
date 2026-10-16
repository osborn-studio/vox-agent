"""Tests for engine.task_queue_stats — real-time queue metrics."""

import pytest
from datetime import datetime, timezone
from engine.task_queue_stats import QueueStats
from engine.store import StoreManager
from engine.models import Task, TaskStatus


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    stats = QueueStats(store)
    yield stats, store
    store.close()


class TestQueueStats:
    def test_empty_depth(self, components):
        stats, _ = components
        assert stats.queue_depth() == {}

    def test_depth_by_status(self, components):
        stats, store = components
        store.insert_task(Task(agent_id="claude", title="a"))
        store.insert_task(Task(agent_id="claude", title="b", status=TaskStatus.RUNNING))
        store.insert_task(Task(agent_id="claude", title="c", status=TaskStatus.COMPLETED))
        depth = stats.queue_depth()
        assert depth.get("draft", 0) == 1
        assert depth.get("running", 0) == 1
        assert depth.get("completed", 0) == 1

    def test_throughput_empty(self, components):
        stats, _ = components
        tp = stats.throughput()
        assert tp["tasks_completed"] == 0

    def test_throughput(self, components):
        stats, store = components
        for i in range(5):
            t = Task(agent_id="claude", title=f"t{i}")
            store.insert_task(t)
            store.update_task_status(
                t.id, TaskStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        tp = stats.throughput(minutes=60)
        assert tp["tasks_completed"] == 5
        assert tp["per_minute"] > 0

    def test_agent_utilization(self, components):
        stats, store = components
        store.insert_task(Task(agent_id="claude", title="a", status=TaskStatus.RUNNING))
        store.insert_task(Task(agent_id="claude", title="b", status=TaskStatus.COMPLETED))
        store.insert_task(Task(agent_id="codex", title="c"))
        util = stats.agent_utilization()
        assert len(util) == 2

    def test_wait_time(self, components):
        stats, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        store.update_task_status(
            t.id, TaskStatus.RUNNING,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        avg = stats.avg_wait_time()
        assert avg >= 0

    def test_summary(self, components):
        stats, _ = components
        s = stats.summary()
        assert "depth" in s
        assert "throughput" in s
        assert "utilization" in s
        assert "avg_wait_seconds" in s
