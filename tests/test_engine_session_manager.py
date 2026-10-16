"""Tests for engine.session_manager — user interaction sessions."""

import pytest
from engine.session_manager import SessionManager
from engine.store import StoreManager
from engine.events import EventBus


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    bus = EventBus()
    mgr = SessionManager(store, bus)
    yield mgr, store, bus
    store.close()


class TestSessionManager:
    def test_start(self, components):
        mgr, _, _ = components
        session = mgr.start()
        assert session.id
        assert mgr.is_active

    def test_end(self, components):
        mgr, _, _ = components
        mgr.start()
        session = mgr.end()
        assert session is not None
        assert session.duration_seconds >= 0
        assert not mgr.is_active

    def test_end_without_start(self, components):
        mgr, _, _ = components
        assert mgr.end() is None

    @pytest.mark.asyncio
    async def test_task_tracking(self, components):
        mgr, _, bus = components
        mgr.start()
        await bus.emit("task:created", {"agent_id": "claude"})
        await bus.emit("task:created", {"agent_id": "codex"})
        session = mgr.end()
        assert session.task_count == 2
        assert set(session.agents_used) == {"claude", "codex"}

    @pytest.mark.asyncio
    async def test_message_tracking(self, components):
        mgr, _, bus = components
        mgr.start()
        await bus.emit("task:message", {})
        await bus.emit("task:message", {})
        await bus.emit("task:message", {})
        session = mgr.end()
        assert session.message_count == 3

    def test_auto_end_previous(self, components):
        mgr, _, _ = components
        s1 = mgr.start()
        s2 = mgr.start()  # should end s1
        assert s2.id != s1.id
        assert mgr.current.id == s2.id

    def test_activity_log(self, components):
        mgr, store, _ = components
        mgr.start()
        mgr.end()
        rows = store._activity_db.execute(
            "SELECT * FROM activity_log WHERE action = 'session:ended'"
        ).fetchall()
        assert len(rows) == 1
