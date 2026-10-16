"""Tests for engine.orchestrator — message routing and intent handling."""

import asyncio
import pytest
from unittest.mock import AsyncMock

from engine.orchestrator import Orchestrator
from engine.intent import IntentParser
from engine.scheduler import TaskScheduler
from engine.events import EventBus
from engine.engine import AgentEngine
from engine.store import StoreManager
from engine.models import EngineEvent, EventType, TaskStatus


@pytest.fixture
def components(tmp_path):
    bus = EventBus()
    store = StoreManager(tmp_path)
    store.connect()

    engine = AsyncMock(spec=AgentEngine)

    async def instant_done(agent_id, prompt, **kwargs):
        yield EngineEvent(type=EventType.TEXT_DELTA, data="Done.")
        yield EngineEvent(type=EventType.DONE, result={})

    engine.run = instant_done
    engine.stop = AsyncMock()

    scheduler = TaskScheduler(bus, engine, store)
    parser = IntentParser()
    orch = Orchestrator(parser, scheduler, bus, store)
    yield orch, store, scheduler
    store.close()


class TestRecordIntent:
    @pytest.mark.asyncio
    async def test_note_recorded(self, components):
        orch, store, _ = components
        r = await orch.process_message("记一下：明天开会")
        assert r.action == "recorded"

    @pytest.mark.asyncio
    async def test_no_agent_defaults_record(self, components):
        orch, _, _ = components
        r = await orch.process_message("今天天气不错", agent_id=None)
        assert r.action == "recorded"


class TestTaskIntent:
    @pytest.mark.asyncio
    async def test_task_created(self, components):
        orch, store, _ = components
        r = await orch.process_message("帮我写一个登录接口", agent_id="claude-code")
        assert r.action == "task_created"
        assert r.task_id is not None
        await asyncio.sleep(0.5)
        task = store.get_task(r.task_id)
        assert task is not None

    @pytest.mark.asyncio
    async def test_task_no_agent(self, components):
        orch, _, _ = components
        r = await orch.process_message("帮我修 bug", agent_id=None)
        assert r.action == "recorded"
        assert "agent" in r.message.lower()

    @pytest.mark.asyncio
    async def test_urgent_priority(self, components):
        orch, store, _ = components
        r = await orch.process_message("紧急修复线上 bug", agent_id="claude")
        assert r.action == "task_created"
        await asyncio.sleep(0.3)
        task = store.get_task(r.task_id)
        assert task["priority"] == 3  # urgent


class TestQueryIntent:
    @pytest.mark.asyncio
    async def test_query_no_tasks(self, components):
        orch, _, _ = components
        r = await orch.process_message("进度怎么样了", agent_id="claude")
        assert r.action == "status"
        assert "no active" in r.message.lower()

    @pytest.mark.asyncio
    async def test_query_with_active_task(self, components):
        orch, store, scheduler = components
        # Create a task first
        await scheduler.create_task(
            agent_id="claude", title="test task", prompt="test",
            auto_dispatch=False,
        )
        store.update_task_status(
            store.list_tasks()[0]["id"], TaskStatus.ASSIGNED,
        )
        r = await orch.process_message("做到哪了", agent_id="claude")
        assert r.action == "status"

    @pytest.mark.asyncio
    async def test_query_all_agents(self, components):
        orch, _, _ = components
        r = await orch.process_message("进度怎么样了", agent_id=None)
        assert r.action == "status"


class TestConfirmIntent:
    @pytest.mark.asyncio
    async def test_confirm_no_tasks(self, components):
        orch, _, _ = components
        r = await orch.process_message("好的", agent_id="claude")
        assert r.action == "confirmed"

    @pytest.mark.asyncio
    async def test_confirm_completed_task(self, components):
        orch, store, scheduler = components
        task = await scheduler.create_task(
            agent_id="claude", title="done", prompt="test",
            auto_dispatch=False,
        )
        store.update_task_status(task.id, TaskStatus.COMPLETED)
        r = await orch.process_message("通过", agent_id="claude")
        assert r.action == "confirmed"
        assert r.task_id == task.id


class TestCancelIntent:
    @pytest.mark.asyncio
    async def test_cancel_no_tasks(self, components):
        orch, _, _ = components
        r = await orch.process_message("取消", agent_id="claude")
        assert r.action == "cancelled"
        assert "no active" in r.message.lower()


class TestChatIntent:
    @pytest.mark.asyncio
    async def test_chat_creates_task(self, components):
        orch, store, _ = components
        r = await orch.process_message(
            "你觉得这个方案怎么样", agent_id="claude-code"
        )
        assert r.action == "chat"
        assert r.task_id is not None

    @pytest.mark.asyncio
    async def test_chat_no_agent_falls_to_record(self, components):
        orch, _, _ = components
        r = await orch.process_message(
            "你觉得这个方案怎么样", agent_id=None
        )
        # Without agent_id, chat intent falls back to record
        assert r.action == "recorded"
