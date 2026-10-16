"""Task Scheduler — manages task lifecycle (Multica-style state machine)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .models import (
    Task, TaskMessage, TaskStatus, TaskPriority,
    ActivityEntry, EngineEvent, EventType,
)
from .events import EventBus
from .engine import AgentEngine
from .store import StoreManager
from .concurrency import ConcurrencyController
from .retry import RetryConfig, with_retry

logger = logging.getLogger(__name__)


class TaskScheduler:
    """Create, dispatch, and track tasks through the state machine.

    State flow: draft → planned → assigned → running → completed/failed/blocked/cancelled
    """

    def __init__(
        self,
        event_bus: EventBus,
        engine: AgentEngine,
        store: StoreManager,
        concurrency: ConcurrencyController | None = None,
        retry_config: RetryConfig | None = None,
        retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.event_bus = event_bus
        self.engine = engine
        self.store = store
        self.concurrency = concurrency or ConcurrencyController(global_max=10)
        self.retry_config = retry_config or RetryConfig()
        self.retry_sleep = retry_sleep
        self._msg_seq: dict[str, int] = {}  # task_id → next seq number
        self._dispatch_tasks: dict[str, asyncio.Task[None]] = {}
        self._pause_gates: dict[str, asyncio.Event] = {}

    async def create_task(
        self,
        agent_id: str,
        title: str,
        prompt: str,
        *,
        description: str = "",
        priority: TaskPriority = TaskPriority.MEDIUM,
        trigger_message_id: str | None = None,
        source_agent: str | None = None,
        provider: str = "claude",
        auto_dispatch: bool = True,
        task_id: str | None = None,
        requester_user_id: str = "",
        workspace_id: str = "",
        working_directory: str | None = None,
    ) -> Task:
        task = Task(
            agent_id=agent_id,
            title=title,
            prompt=prompt,
            description=description,
            priority=priority,
            trigger_message_id=trigger_message_id,
            source_agent=source_agent,
            requester_user_id=requester_user_id,
            workspace_id=workspace_id,
            working_directory=working_directory,
        )
        if task_id is not None:
            task.id = task_id
        self.store.insert_task(task)
        await self.event_bus.emit("task:created", task, actor_type="system")

        self.store.insert_activity(ActivityEntry(
            actor_type="system",
            actor_id="scheduler",
            action="task:created",
            target_type="task",
            target_id=task.id,
            details={"title": title, "agent_id": agent_id},
        ))

        if auto_dispatch:
            gate = asyncio.Event()
            gate.set()
            self._pause_gates[task.id] = gate
            dispatch = asyncio.create_task(self._dispatch(task, provider))
            self._dispatch_tasks[task.id] = dispatch
            dispatch.add_done_callback(
                lambda completed, dispatched_id=task.id: self._discard_dispatch(
                    dispatched_id, completed
                )
            )

        return task

    async def cancel_task(self, task_id: str) -> bool:
        from .models import VALID_TRANSITIONS

        row = self.store.get_task(task_id)
        if not row:
            return False
        status = TaskStatus(row["status"])
        allowed = VALID_TRANSITIONS.get(status, set())
        if TaskStatus.CANCELLED not in allowed:
            return False
        await self._transition_task(task_id, TaskStatus.CANCELLED)
        await self.engine.stop(task_id)
        dispatch = self._dispatch_tasks.get(task_id)
        if dispatch is not None and dispatch is not asyncio.current_task():
            dispatch.cancel()
        return True

    async def pause_task(self, task_id: str) -> bool:
        row = self.store.get_task(task_id)
        if not row or TaskStatus(row["status"]) != TaskStatus.RUNNING:
            return False
        gate = self._pause_gates.get(task_id)
        if gate is None:
            return False
        gate.clear()
        await self._transition_task(task_id, TaskStatus.BLOCKED)
        return True

    async def resume_task(self, task_id: str) -> bool:
        row = self.store.get_task(task_id)
        if not row or TaskStatus(row["status"]) != TaskStatus.BLOCKED:
            return False
        gate = self._pause_gates.get(task_id)
        if gate is None:
            return False
        await self._transition_task(task_id, TaskStatus.RUNNING)
        gate.set()
        return True

    def _discard_dispatch(
        self, task_id: str, completed: asyncio.Task[None]
    ) -> None:
        if self._dispatch_tasks.get(task_id) is completed:
            self._dispatch_tasks.pop(task_id, None)

    # -- internal dispatch ---------------------------------------------------

    async def _dispatch(self, task: Task, provider: str) -> None:
        task_id = task.id
        gate = self._pause_gates.get(task_id)
        if gate is None:
            gate = asyncio.Event()
            gate.set()
            self._pause_gates[task_id] = gate
        try:
            await self._transition_task(task_id, TaskStatus.ASSIGNED)

            # Acquire concurrency slot (global + per-agent limit)
            async with self.concurrency.acquire(task.agent_id):
                await self._transition_task(
                    task_id, TaskStatus.RUNNING,
                    started_at=datetime.now(timezone.utc).isoformat(),
                )
                await gate.wait()

                events, first_event = await with_retry(
                    self._start_engine,
                    task_id,
                    task.agent_id,
                    task.prompt,
                    provider,
                    task.requester_user_id,
                    task.workspace_id,
                    task.working_directory,
                    config=self.retry_config,
                    on_retry=lambda attempt, error, delay: self._record_retry(
                        task_id, attempt, error, delay
                    ),
                    sleep_func=self.retry_sleep,
                )
                await gate.wait()
                await self._handle_event(task_id, task.agent_id, first_event)
                async for event in events:
                    await gate.wait()
                    await self._handle_event(task_id, task.agent_id, event)

            await gate.wait()
            await self._transition_task(
                task_id, TaskStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        except asyncio.CancelledError:
            row = self.store.get_task(task_id)
            if row and TaskStatus(row["status"]) != TaskStatus.CANCELLED:
                await self._transition_task(task_id, TaskStatus.CANCELLED)
            raise
        except Exception as e:
            logger.exception("task %s failed", task_id)
            await self._transition_task(
                task_id, TaskStatus.FAILED,
                error=str(e),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            self._pause_gates.pop(task_id, None)

    async def _start_engine(
        self,
        task_id: str,
        agent_id: str,
        prompt: str,
        provider: str,
        requester_user_id: str,
        workspace_id: str,
        working_directory: str | None = None,
    ) -> tuple[AsyncIterator[EngineEvent], EngineEvent]:
        """Open an engine stream and obtain its first event without persisting it.

        Retrying is safe only in this window: after the first event, a provider
        may already have run tools or the scheduler may have stored messages.
        """
        directory_kwargs = {"working_directory": working_directory} if working_directory else {}
        events = self.engine.run(
            agent_id,
            prompt,
            task_id=task_id,
            provider=provider,
            requester_user_id=requester_user_id,
            workspace_id=workspace_id,
            **directory_kwargs,
        )
        first_event = await anext(events)
        return events, first_event

    async def _record_retry(
        self,
        task_id: str,
        attempt: int,
        error: Exception,
        delay: float,
    ) -> None:
        details = {
            "attempt": attempt,
            "delay_seconds": delay,
            "error_type": type(error).__name__,
        }
        await self.event_bus.emit(
            "task:retry", {"task_id": task_id, **details}
        )
        self.store.insert_activity(ActivityEntry(
            actor_type="system",
            actor_id="scheduler",
            action="task:retry",
            target_type="task",
            target_id=task_id,
            details=details,
        ))

    async def _handle_event(
        self, task_id: str, agent_id: str, event: EngineEvent
    ) -> None:
        seq = self._next_seq(task_id)

        match event.type:
            case EventType.TEXT_DELTA:
                msg = TaskMessage(
                    task_id=task_id, seq=seq,
                    author_type="agent", author_id=agent_id,
                    type="text", content=event.data,
                )
                self.store.insert_task_message(msg)
                await self.event_bus.emit("task:message", msg)

            case EventType.TOOL_USE:
                msg = TaskMessage(
                    task_id=task_id, seq=seq,
                    author_type="agent", author_id=agent_id,
                    type="tool_use", tool=event.tool, input=event.input,
                )
                self.store.insert_task_message(msg)
                await self.event_bus.emit("task:message", msg)

            case EventType.TOOL_DONE:
                msg = TaskMessage(
                    task_id=task_id, seq=seq,
                    author_type="agent", author_id=agent_id,
                    type="tool_result", tool=event.tool, output=event.output,
                )
                self.store.insert_task_message(msg)
                await self.event_bus.emit("task:message", msg)

            case EventType.ERROR:
                msg = TaskMessage(
                    task_id=task_id, seq=seq,
                    author_type="system", author_id="engine",
                    type="error", content=event.data,
                )
                self.store.insert_task_message(msg)
                await self.event_bus.emit("task:message", msg)

            case EventType.DONE:
                await self.event_bus.emit(
                    "task:progress",
                    {"task_id": task_id, "status": "done", "result": event.result},
                )

    async def _transition_task(
        self, task_id: str, status: TaskStatus, **kwargs: Any
    ) -> None:
        self.store.update_task_status(task_id, status, **kwargs)
        event_map = {
            TaskStatus.ASSIGNED: "task:dispatched",
            TaskStatus.RUNNING: "task:progress",
            TaskStatus.COMPLETED: "task:completed",
            TaskStatus.FAILED: "task:failed",
            TaskStatus.CANCELLED: "task:cancelled",
        }
        event_type = event_map.get(status, "task:progress")
        await self.event_bus.emit(
            event_type,
            {"task_id": task_id, "status": status.value, **kwargs},
        )

        self.store.insert_activity(ActivityEntry(
            actor_type="system",
            actor_id="scheduler",
            action=event_type,
            target_type="task",
            target_id=task_id,
            details={"status": status.value},
        ))

    def _next_seq(self, task_id: str) -> int:
        seq = self._msg_seq.get(task_id, 0) + 1
        self._msg_seq[task_id] = seq
        return seq
