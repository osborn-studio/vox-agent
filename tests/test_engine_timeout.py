"""Tests for engine.timeout — task timeout enforcement."""

import pytest
from datetime import datetime, timezone, timedelta
from engine.timeout import TimeoutManager
from engine.store import StoreManager
from engine.events import EventBus
from engine.models import Task, TaskStatus


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    bus = EventBus()
    mgr = TimeoutManager(store, bus, default_timeout=60, check_interval=1)
    yield mgr, store, bus
    store.close()


class TestTimeoutManager:
    @pytest.mark.asyncio
    async def test_no_running_tasks(self, components):
        mgr, _, _ = components
        timed_out = await mgr.check_timeouts()
        assert timed_out == []

    @pytest.mark.asyncio
    async def test_task_within_timeout(self, components):
        mgr, store, _ = components
        t = Task(agent_id="claude", title="ok")
        store.insert_task(t)
        store.update_task_status(
            t.id, TaskStatus.RUNNING,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        timed_out = await mgr.check_timeouts()
        assert timed_out == []

    @pytest.mark.asyncio
    async def test_task_exceeded_timeout(self, components):
        mgr, store, bus = components
        events = []
        bus.on("task:failed", lambda t, p: events.append(p))

        t = Task(agent_id="claude", title="slow")
        store.insert_task(t)
        # Started 2 minutes ago (timeout is 60s)
        old_start = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        store.update_task_status(t.id, TaskStatus.RUNNING, started_at=old_start)

        timed_out = await mgr.check_timeouts()
        assert t.id in timed_out

        # Check it was marked as failed
        task = store.get_task(t.id)
        assert task["status"] == "failed"
        assert "timeout" in task["error"]

        # Check event emitted
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_custom_timeout(self, components):
        mgr, store, _ = components
        t = Task(agent_id="claude", title="custom")
        store.insert_task(t)
        old_start = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        store.update_task_status(t.id, TaskStatus.RUNNING, started_at=old_start)

        # Default timeout (60s) → should not timeout
        timed_out = await mgr.check_timeouts()
        assert t.id not in timed_out

        # Set custom timeout of 10s → should timeout
        mgr.set_timeout(t.id, 10)
        timed_out = await mgr.check_timeouts()
        assert t.id in timed_out

    @pytest.mark.asyncio
    async def test_completed_not_checked(self, components):
        mgr, store, _ = components
        t = Task(agent_id="claude", title="done")
        store.insert_task(t)
        store.update_task_status(t.id, TaskStatus.COMPLETED)
        timed_out = await mgr.check_timeouts()
        assert timed_out == []

    @pytest.mark.asyncio
    async def test_multiple_timeouts(self, components):
        mgr, store, _ = components
        old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        for i in range(3):
            t = Task(agent_id="claude", title=f"slow-{i}")
            store.insert_task(t)
            store.update_task_status(t.id, TaskStatus.RUNNING, started_at=old)

        timed_out = await mgr.check_timeouts()
        assert len(timed_out) == 3
