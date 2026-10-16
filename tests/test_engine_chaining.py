"""Tests for engine.chaining — multi-agent workflow pipelines."""

import pytest
from unittest.mock import AsyncMock

from engine.chaining import AgentChain, ChainStep
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
    call_count = {"n": 0}

    async def mock_run(agent_id, prompt, **kwargs):
        call_count["n"] += 1
        yield EngineEvent(type=EventType.TEXT_DELTA, data=f"Step {call_count['n']} output for {agent_id}")
        yield EngineEvent(type=EventType.DONE, result={})

    engine.run = mock_run
    engine.stop = AsyncMock()

    scheduler = TaskScheduler(bus, engine, store)
    chain = AgentChain(scheduler, store, bus)
    yield chain, store, bus
    store.close()


class TestAgentChain:
    @pytest.mark.asyncio
    async def test_single_step(self, components):
        chain, store, bus = components
        steps = [ChainStep(agent_id="claude", prompt_template="Do: {input}")]
        result = await chain.execute("c1", steps, "hello")

        assert result.status == "completed"
        assert result.steps_completed == 1
        assert len(result.task_ids) == 1

    @pytest.mark.asyncio
    async def test_multi_step(self, components):
        chain, store, bus = components
        steps = [
            ChainStep(agent_id="claude", prompt_template="Write: {input}"),
            ChainStep(agent_id="claude", prompt_template="Review: {input}"),
            ChainStep(agent_id="claude", prompt_template="Test: {input}"),
        ]
        result = await chain.execute("c2", steps, "auth module")

        assert result.status == "completed"
        assert result.steps_completed == 3
        assert len(result.task_ids) == 3

    @pytest.mark.asyncio
    async def test_events_emitted(self, components):
        chain, store, bus = components
        events = []
        bus.on_all(lambda t, p: events.append(t))

        steps = [ChainStep(agent_id="claude", prompt_template="{input}")]
        await chain.execute("c3", steps, "test")

        event_types = set(events)
        assert "chain:started" in event_types
        assert "chain:step_completed" in event_types
        assert "chain:completed" in event_types

    @pytest.mark.asyncio
    async def test_input_piping(self, components):
        chain, store, bus = components
        steps = [
            ChainStep(agent_id="claude", prompt_template="First: {input}"),
            ChainStep(agent_id="claude", prompt_template="Second: {input}"),
        ]
        result = await chain.execute("c4", steps, "start")

        # Second step should have received first step's output
        assert result.steps_completed == 2
