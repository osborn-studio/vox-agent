"""Tests for engine.replay — event replay system."""

import pytest
from engine.replay import EventReplay
from engine.store import StoreManager
from engine.events import EventBus
from engine.models import Task, TaskStatus, TaskMessage


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    bus = EventBus()
    replay = EventReplay(store, bus)

    # Create a task with messages
    t = Task(agent_id="claude", title="test task")
    store.insert_task(t)
    store.update_task_status(t.id, TaskStatus.COMPLETED)

    for i in range(5):
        store.insert_task_message(TaskMessage(
            task_id=t.id, seq=i + 1,
            author_type="agent", author_id="claude",
            type="text", content=f"Step {i + 1}",
        ))

    yield replay, store, bus, t.id
    store.close()


class TestReplay:
    @pytest.mark.asyncio
    async def test_replay(self, components):
        replay, _, bus, task_id = components
        received = []
        bus.on("task:message", lambda t, p: received.append(p))

        count = await replay.replay(task_id, speed=10.0, delay_between_ms=10)
        assert count == 5
        assert len(received) == 5
        assert received[0]["replay"] is True

    @pytest.mark.asyncio
    async def test_replay_events(self, components):
        replay, _, bus, task_id = components
        events = []
        bus.on_all(lambda t, p: events.append(t))

        await replay.replay(task_id, speed=10.0, delay_between_ms=10)
        event_types = set(events)
        assert "replay:started" in event_types
        assert "replay:completed" in event_types

    @pytest.mark.asyncio
    async def test_replay_empty(self, components):
        replay, store, _, _ = components
        t = Task(agent_id="claude", title="empty")
        store.insert_task(t)
        count = await replay.replay(t.id)
        assert count == 0

    @pytest.mark.asyncio
    async def test_summary(self, components):
        replay, _, _, task_id = components
        summary = await replay.replay_summary(task_id)
        assert summary is not None
        assert summary["message_count"] == 5
        assert summary["message_types"]["text"] == 5

    @pytest.mark.asyncio
    async def test_summary_nonexistent(self, components):
        replay, _, _, _ = components
        assert await replay.replay_summary("nope") is None

    @pytest.mark.asyncio
    async def test_replay_ws_receives(self, components):
        replay, _, bus, task_id = components
        q = bus.subscribe_ws()
        await replay.replay(task_id, speed=10.0, delay_between_ms=10)

        ws_events = []
        while not q.empty():
            ws_events.append(q.get_nowait())

        assert len(ws_events) >= 7  # 1 started + 5 messages + 1 completed
