"""Integration tests for engine.scheduler — task lifecycle with mock engine."""

import asyncio
import pytest
from unittest.mock import AsyncMock

from engine.scheduler import TaskScheduler
from engine.events import EventBus
from engine.engine import AgentEngine
from engine.store import StoreManager
from engine.models import (
    TaskStatus, TaskPriority,
    EngineEvent, EventType,
)
from engine.retry import RetryConfig


@pytest.fixture
def components(tmp_path):
    bus = EventBus()
    store = StoreManager(tmp_path)
    store.connect()

    # mock engine that yields a sequence of events
    engine = AsyncMock(spec=AgentEngine)

    async def mock_run(agent_id, prompt, **kwargs):
        yield EngineEvent(type=EventType.TEXT_DELTA, data="thinking...")
        yield EngineEvent(type=EventType.TOOL_USE, tool="bash", input={"command": "echo hi"})
        yield EngineEvent(type=EventType.TOOL_DONE, tool="bash", output="hi\n")
        yield EngineEvent(type=EventType.TEXT_DELTA, data="done!")
        yield EngineEvent(type=EventType.DONE, result={"exit_code": 0})

    engine.run = mock_run
    engine.stop = AsyncMock()

    scheduler = TaskScheduler(bus, engine, store)
    yield scheduler, bus, store
    store.close()


class TestTaskLifecycle:
    @pytest.mark.asyncio
    async def test_cancel_stops_dispatch_and_never_completes_afterward(self, tmp_path):
        bus = EventBus()
        store = StoreManager(tmp_path)
        store.connect()
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingEngine:
            def __init__(self):
                self.stopped = []

            async def run(self, agent_id, prompt, **kwargs):
                started.set()
                yield EngineEvent(type=EventType.TEXT_DELTA, data="working")
                await release.wait()
                yield EngineEvent(type=EventType.DONE, result={})

            async def stop(self, task_id):
                self.stopped.append(task_id)

        engine = BlockingEngine()
        scheduler = TaskScheduler(bus, engine, store)
        task = await scheduler.create_task(
            agent_id="codex", title="cancel me", prompt="wait"
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0)

        assert await scheduler.cancel_task(task.id)
        release.set()
        await asyncio.sleep(0.05)

        assert store.get_task(task.id)["status"] == "cancelled"
        assert engine.stopped == [task.id]
        store.close()

    @pytest.mark.asyncio
    async def test_pause_blocks_next_event_until_resume(self, tmp_path):
        bus = EventBus()
        store = StoreManager(tmp_path)
        store.connect()
        first_sent = asyncio.Event()
        allow_second = asyncio.Event()

        class PausableEngine:
            async def run(self, agent_id, prompt, **kwargs):
                yield EngineEvent(type=EventType.TEXT_DELTA, data="first")
                first_sent.set()
                await allow_second.wait()
                yield EngineEvent(type=EventType.TEXT_DELTA, data="second")
                yield EngineEvent(type=EventType.DONE, result={})

            async def stop(self, task_id):
                return None

        scheduler = TaskScheduler(bus, PausableEngine(), store)
        task = await scheduler.create_task(
            agent_id="codex", title="pause me", prompt="wait"
        )
        await asyncio.wait_for(first_sent.wait(), timeout=1)

        assert await scheduler.pause_task(task.id)
        allow_second.set()
        await asyncio.sleep(0.05)
        assert store.get_task(task.id)["status"] == "blocked"
        assert [row["content"] for row in store.list_task_messages(task.id)] == ["first"]

        assert await scheduler.resume_task(task.id)
        await asyncio.sleep(0.05)
        assert store.get_task(task.id)["status"] == "completed"
        assert [row["content"] for row in store.list_task_messages(task.id)] == [
            "first", "second",
        ]
        store.close()

    @pytest.mark.asyncio
    async def test_caller_can_supply_stable_task_id(self, components):
        scheduler, _, store = components
        task = await scheduler.create_task(
            agent_id="codex",
            title="stable id",
            prompt="run",
            task_id="swift-task-123",
            auto_dispatch=False,
        )

        assert task.id == "swift-task-123"
        assert store.get_task("swift-task-123")["title"] == "stable id"

    @pytest.mark.asyncio
    async def test_engine_receives_task_context_for_permission_requests(self, tmp_path):
        bus = EventBus()
        store = StoreManager(tmp_path)
        store.connect()
        captured = {}

        class CapturingEngine:
            async def run(self, agent_id, prompt, **kwargs):
                captured.update(kwargs)
                yield EngineEvent(type=EventType.DONE, result={})

            async def stop(self, task_id):
                return None

        scheduler = TaskScheduler(bus, CapturingEngine(), store)
        task = await scheduler.create_task(
            agent_id="codex",
            title="permission context",
            prompt="edit the file",
            requester_user_id="user-123",
            workspace_id="workspace-456",
            auto_dispatch=False,
        )

        await scheduler._dispatch(task, "direct-provider")

        assert captured["task_id"] == task.id
        assert captured["requester_user_id"] == "user-123"
        assert captured["workspace_id"] == "workspace-456"
        stored = store.get_task(task.id)
        assert stored["requester_user_id"] == "user-123"
        assert stored["workspace_id"] == "workspace-456"
        store.close()

    @pytest.mark.asyncio
    async def test_full_happy_path(self, components):
        scheduler, bus, store = components

        received_events = []
        bus.on_all(lambda t, p: received_events.append(t))

        task = await scheduler.create_task(
            agent_id="claude-code",
            title="Run tests",
            prompt="pytest -v",
        )
        assert task.status == TaskStatus.DRAFT

        # wait for async dispatch
        await asyncio.sleep(0.5)

        # check final status
        row = store.get_task(task.id)
        assert row["status"] == "completed"

        # check messages were recorded
        msgs = store.list_task_messages(task.id)
        assert len(msgs) >= 4  # text + tool_use + tool_result + text

        # check event types received
        event_types = set(received_events)
        assert "task:created" in event_types
        assert "task:dispatched" in event_types
        assert "task:message" in event_types
        assert "task:completed" in event_types

    @pytest.mark.asyncio
    async def test_task_messages_have_correct_types(self, components):
        scheduler, bus, store = components

        task = await scheduler.create_task(
            agent_id="claude",
            title="test",
            prompt="hello",
        )
        await asyncio.sleep(0.5)

        msgs = store.list_task_messages(task.id)
        types = [m["type"] for m in msgs]
        assert "text" in types
        assert "tool_use" in types
        assert "tool_result" in types

    @pytest.mark.asyncio
    async def test_task_messages_sequential(self, components):
        scheduler, bus, store = components

        task = await scheduler.create_task(
            agent_id="claude",
            title="test",
            prompt="hello",
        )
        await asyncio.sleep(0.5)

        msgs = store.list_task_messages(task.id)
        seqs = [m["seq"] for m in msgs]
        assert seqs == sorted(seqs)
        assert seqs[0] == 1

    @pytest.mark.asyncio
    async def test_ws_receives_events(self, components):
        scheduler, bus, store = components

        q = bus.subscribe_ws()
        task = await scheduler.create_task(
            agent_id="claude",
            title="ws test",
            prompt="hello",
        )
        await asyncio.sleep(0.5)

        ws_events = []
        while not q.empty():
            ws_events.append(q.get_nowait())

        ws_types = {e["type"] for e in ws_events}
        assert "task:created" in ws_types
        assert "task:message" in ws_types
        assert "task:completed" in ws_types

    @pytest.mark.asyncio
    async def test_activity_log_written(self, components):
        scheduler, bus, store = components

        task = await scheduler.create_task(
            agent_id="claude",
            title="log test",
            prompt="hello",
        )
        await asyncio.sleep(0.5)

        # activity.db should have entries
        rows = store._activity_db.execute(
            "SELECT * FROM activity_log WHERE target_id = ?", (task.id,)
        ).fetchall()
        assert len(rows) >= 2  # at least created + completed


class TestTaskFailure:
    @pytest.mark.asyncio
    async def test_engine_error_marks_failed(self, tmp_path):
        bus = EventBus()
        store = StoreManager(tmp_path)
        store.connect()

        engine = AsyncMock(spec=AgentEngine)

        async def failing_run(agent_id, prompt, **kwargs):
            yield EngineEvent(type=EventType.TEXT_DELTA, data="starting...")
            raise RuntimeError("LLM API down")

        engine.run = failing_run
        engine.stop = AsyncMock()
        scheduler = TaskScheduler(bus, engine, store)

        task = await scheduler.create_task(
            agent_id="claude",
            title="will fail",
            prompt="boom",
        )
        await asyncio.sleep(0.5)

        row = store.get_task(task.id)
        assert row["status"] == "failed"
        assert "LLM API down" in (row["error"] or "")
        store.close()

    @pytest.mark.asyncio
    async def test_transient_start_failure_retries_before_first_event(self, tmp_path):
        bus = EventBus()
        store = StoreManager(tmp_path)
        store.connect()
        attempts = 0
        delays = []

        class FlakyEngine:
            async def run(self, agent_id, prompt, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise ConnectionError("provider unavailable")
                yield EngineEvent(type=EventType.TEXT_DELTA, data="recovered")
                yield EngineEvent(type=EventType.DONE, result={"ok": True})

            async def stop(self, task_id):
                return None

        async def fake_sleep(delay):
            delays.append(delay)

        scheduler = TaskScheduler(
            bus,
            FlakyEngine(),
            store,
            retry_config=RetryConfig(max_retries=3, base_delay=1, backoff_factor=2),
            retry_sleep=fake_sleep,
        )
        task = await scheduler.create_task(
            agent_id="claude", title="retry", prompt="try", auto_dispatch=False
        )

        await scheduler._dispatch(task, "claude")

        assert attempts == 3
        assert delays == [1, 2]
        assert store.get_task(task.id)["status"] == "completed"
        assert [m["content"] for m in store.list_task_messages(task.id)] == ["recovered"]
        retry_actions = store._activity_db.execute(
            "SELECT details FROM activity_log WHERE target_id = ? AND action = 'task:retry'",
            (task.id,),
        ).fetchall()
        assert len(retry_actions) == 2
        store.close()

    @pytest.mark.asyncio
    async def test_failure_after_first_event_is_not_retried(self, tmp_path):
        bus = EventBus()
        store = StoreManager(tmp_path)
        store.connect()
        attempts = 0

        class PartialEngine:
            async def run(self, agent_id, prompt, **kwargs):
                nonlocal attempts
                attempts += 1
                yield EngineEvent(type=EventType.TEXT_DELTA, data="already persisted")
                raise ConnectionError("stream dropped")

            async def stop(self, task_id):
                return None

        async def fail_if_slept(delay):
            pytest.fail("a partially consumed stream must not be retried")

        scheduler = TaskScheduler(
            bus,
            PartialEngine(),
            store,
            retry_config=RetryConfig(max_retries=3),
            retry_sleep=fail_if_slept,
        )
        task = await scheduler.create_task(
            agent_id="claude", title="no duplicate", prompt="try", auto_dispatch=False
        )

        await scheduler._dispatch(task, "claude")

        assert attempts == 1
        assert store.get_task(task.id)["status"] == "failed"
        assert [m["content"] for m in store.list_task_messages(task.id)] == [
            "already persisted"
        ]
        store.close()


class TestTaskPriority:
    @pytest.mark.asyncio
    async def test_priority_stored(self, components):
        scheduler, bus, store = components

        task = await scheduler.create_task(
            agent_id="claude",
            title="urgent task",
            prompt="fix production",
            priority=TaskPriority.URGENT,
        )
        await asyncio.sleep(0.3)

        row = store.get_task(task.id)
        assert row["priority"] == TaskPriority.URGENT.value


class TestNoAutoDispatch:
    @pytest.mark.asyncio
    async def test_manual_dispatch(self, components):
        scheduler, bus, store = components

        task = await scheduler.create_task(
            agent_id="claude",
            title="manual",
            prompt="wait",
            auto_dispatch=False,
        )
        await asyncio.sleep(0.3)

        row = store.get_task(task.id)
        assert row["status"] == "draft"  # should NOT auto-dispatch
