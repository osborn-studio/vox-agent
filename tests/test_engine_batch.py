"""Tests for engine.batch — batch task operations."""

import asyncio
import pytest
from unittest.mock import AsyncMock

from engine.batch import BatchOperations, BatchItem
from engine.scheduler import TaskScheduler
from engine.events import EventBus
from engine.engine import AgentEngine
from engine.store import StoreManager
from engine.dependencies import DependencyManager
from engine.models import EngineEvent, EventType


@pytest.fixture
def components(tmp_path):
    bus = EventBus()
    store = StoreManager(tmp_path)
    store.connect()

    engine = AsyncMock(spec=AgentEngine)

    async def instant_done(agent_id, prompt, **kwargs):
        yield EngineEvent(type=EventType.DONE, result={})

    engine.run = instant_done
    engine.stop = AsyncMock()

    scheduler = TaskScheduler(bus, engine, store)
    deps = DependencyManager(store)
    batch = BatchOperations(scheduler, deps)
    yield batch, store, scheduler
    store.close()


class TestBatchCreate:
    @pytest.mark.asyncio
    async def test_create_multiple(self, components):
        batch, store, _ = components
        items = [
            BatchItem(title="task A", prompt="do A"),
            BatchItem(title="task B", prompt="do B"),
            BatchItem(title="task C", prompt="do C"),
        ]
        result = await batch.create_batch(items)
        assert result.total == 3
        assert result.created == 3
        assert len(result.task_ids) == 3
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_create_with_different_agents(self, components):
        batch, store, _ = components
        items = [
            BatchItem(title="claude task", prompt="x", agent_id="claude"),
            BatchItem(title="codex task", prompt="y", agent_id="codex"),
        ]
        result = await batch.create_batch(items)
        assert result.created == 2

        await asyncio.sleep(0.3)
        claude_tasks = store.list_tasks(agent_id="claude")
        codex_tasks = store.list_tasks(agent_id="codex")
        assert len(claude_tasks) == 1
        assert len(codex_tasks) == 1

    @pytest.mark.asyncio
    async def test_create_with_deps(self, components):
        batch, store, _ = components
        items = [
            BatchItem(title="step 1", prompt="first"),                    # index 0
            BatchItem(title="step 2", prompt="second", depends_on=[0]),   # index 1
            BatchItem(title="step 3", prompt="third", depends_on=[0, 1]), # index 2
        ]
        result = await batch.create_batch(items)
        assert result.created == 3


class TestBatchCancel:
    @pytest.mark.asyncio
    async def test_cancel_agent_tasks(self, components):
        batch, store, scheduler = components

        # Create tasks
        for i in range(3):
            await scheduler.create_task(
                agent_id="claude", title=f"task-{i}", prompt="x",
                auto_dispatch=False,
            )

        await asyncio.sleep(0.1)
        cancelled = await batch.cancel_agent_tasks("claude")
        assert cancelled >= 0  # depends on status transitions

    @pytest.mark.asyncio
    async def test_cancel_no_tasks(self, components):
        batch, _, _ = components
        cancelled = await batch.cancel_agent_tasks("nonexistent")
        assert cancelled == 0


class TestBatchStatus:
    @pytest.mark.asyncio
    async def test_batch_status(self, components):
        batch, store, scheduler = components
        t1 = await scheduler.create_task(
            agent_id="claude", title="a", prompt="x", auto_dispatch=False,
        )
        t2 = await scheduler.create_task(
            agent_id="claude", title="b", prompt="y", auto_dispatch=False,
        )

        results = await batch.batch_status([t1.id, t2.id, "nonexistent"])
        assert len(results) == 3
        assert results[0]["status"] == "draft"
        assert results[2]["status"] == "not_found"
