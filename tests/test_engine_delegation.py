"""Tests for engine.delegation — parent-child task management."""

import pytest
from unittest.mock import AsyncMock

from engine.delegation import TaskDelegator, SubtaskSpec
from engine.scheduler import TaskScheduler
from engine.events import EventBus
from engine.engine import AgentEngine
from engine.store import StoreManager
from engine.models import EngineEvent, EventType


@pytest.fixture
def components(tmp_path):
    bus = EventBus()
    store = StoreManager(tmp_path)
    store.connect()

    engine = AsyncMock(spec=AgentEngine)
    async def instant(agent_id, prompt, **kwargs):
        yield EngineEvent(type=EventType.TEXT_DELTA, data="done")
        yield EngineEvent(type=EventType.DONE, result={})
    engine.run = instant
    engine.stop = AsyncMock()

    scheduler = TaskScheduler(bus, engine, store)
    delegator = TaskDelegator(scheduler, store, bus)
    yield delegator, store, bus
    store.close()


class TestDelegation:
    @pytest.mark.asyncio
    async def test_single_subtask(self, components):
        delegator, store, bus = components
        result = await delegator.delegate(
            "Test parent",
            [SubtaskSpec(title="child 1", prompt="do something")],
        )
        assert result.status in ("completed", "partial")
        assert len(result.subtask_ids) == 1

    @pytest.mark.asyncio
    async def test_multiple_subtasks(self, components):
        delegator, store, bus = components
        result = await delegator.delegate(
            "Multi task",
            [
                SubtaskSpec(title="step 1", prompt="first"),
                SubtaskSpec(title="step 2", prompt="second"),
                SubtaskSpec(title="step 3", prompt="third"),
            ],
        )
        assert len(result.subtask_ids) == 3

    @pytest.mark.asyncio
    async def test_parent_child_link(self, components):
        delegator, store, bus = components
        result = await delegator.delegate(
            "Linked",
            [SubtaskSpec(title="child", prompt="test")],
        )
        subtasks = delegator.get_subtasks(result.parent_id)
        assert len(subtasks) >= 1

    @pytest.mark.asyncio
    async def test_get_parent(self, components):
        delegator, store, bus = components
        result = await delegator.delegate(
            "Parent lookup",
            [SubtaskSpec(title="child", prompt="test")],
        )
        parent = delegator.get_parent(result.subtask_ids[0])
        assert parent == result.parent_id

    @pytest.mark.asyncio
    async def test_events_emitted(self, components):
        delegator, store, bus = components
        events = []
        bus.on_all(lambda t, p: events.append(t))

        await delegator.delegate(
            "Event test",
            [SubtaskSpec(title="child", prompt="test")],
        )
        event_types = set(events)
        assert "delegation:started" in event_types
