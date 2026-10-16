"""Task Delegation — parent tasks that spawn and coordinate child tasks.

Enables complex workflows where a parent task breaks down work
into subtasks, assigns them to different agents, and aggregates results.

Example:
  Parent: "Refactor auth module"
    ├── Child 1: "Write new auth code" → claude-code
    ├── Child 2: "Write tests" → claude-code  [depends on Child 1]
    └── Child 3: "Update docs" → claude-code  [depends on Child 1]
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .store import StoreManager
from .scheduler import TaskScheduler
from .events import EventBus
from .models import TaskStatus, TaskPriority

logger = logging.getLogger(__name__)


@dataclass
class SubtaskSpec:
    title: str
    prompt: str
    agent_id: str = "claude-code"
    priority: int = 1
    depends_on: list[int] | None = None  # indices into subtask list


@dataclass
class DelegationResult:
    parent_id: str
    subtask_ids: list[str]
    completed: int
    failed: int
    status: str  # "completed" | "partial" | "failed"


class TaskDelegator:
    """Create parent tasks with subtasks."""

    def __init__(
        self,
        scheduler: TaskScheduler,
        store: StoreManager,
        event_bus: EventBus,
    ) -> None:
        self.scheduler = scheduler
        self.store = store
        self.event_bus = event_bus

    async def delegate(
        self,
        parent_title: str,
        subtasks: list[SubtaskSpec],
        parent_agent: str = "claude-code",
        provider: str = "claude",
    ) -> DelegationResult:
        """Create a parent task with subtasks."""
        # Create parent (doesn't execute, just tracks)
        parent = await self.scheduler.create_task(
            agent_id=parent_agent,
            title=f"[Parent] {parent_title}",
            prompt=f"Delegated: {len(subtasks)} subtasks",
            auto_dispatch=False,
        )

        await self.event_bus.emit("delegation:started", {
            "parent_id": parent.id,
            "subtask_count": len(subtasks),
        })

        # Create subtasks
        subtask_ids: list[str] = []
        for spec in subtasks:
            child = await self.scheduler.create_task(
                agent_id=spec.agent_id,
                title=spec.title,
                prompt=spec.prompt,
                priority=TaskPriority(spec.priority),
                provider=provider,
                auto_dispatch=spec.depends_on is None,  # dispatch if no deps
            )
            subtask_ids.append(child.id)

            # Store parent-child relationship
            self._link_parent(parent.id, child.id)

        # Wait for all to complete
        await asyncio.sleep(0.5)  # let dispatches start
        completed = 0
        failed = 0

        for _ in range(600):  # max 10 minutes
            all_done = True
            completed = 0
            failed = 0

            for sid in subtask_ids:
                task = self.store.get_task(sid)
                if not task:
                    continue
                status = task["status"]
                if status == "completed":
                    completed += 1
                elif status == "failed":
                    failed += 1
                elif status not in ("completed", "failed", "cancelled"):
                    all_done = False

            if all_done:
                break
            await asyncio.sleep(1.0)

        # Update parent status
        if failed == 0 and completed == len(subtasks):
            status = "completed"
            self.store.update_task_status(parent.id, TaskStatus.COMPLETED)
        elif completed > 0:
            status = "partial"
            self.store.update_task_status(parent.id, TaskStatus.COMPLETED)
        else:
            status = "failed"
            self.store.update_task_status(parent.id, TaskStatus.FAILED)

        await self.event_bus.emit("delegation:completed", {
            "parent_id": parent.id,
            "completed": completed,
            "failed": failed,
            "status": status,
        })

        return DelegationResult(
            parent_id=parent.id,
            subtask_ids=subtask_ids,
            completed=completed,
            failed=failed,
            status=status,
        )

    def get_subtasks(self, parent_id: str) -> list[dict]:
        """Get all subtasks for a parent."""
        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            "SELECT child_id FROM task_parent_child WHERE parent_id = ?",
            (parent_id,),
        ).fetchall()
        return [self.store.get_task(r["child_id"]) for r in rows if self.store.get_task(r["child_id"])]

    def get_parent(self, child_id: str) -> str | None:
        """Get parent task ID for a child."""
        self.store._ensure_connected()
        row = self.store._tasks_db.execute(
            "SELECT parent_id FROM task_parent_child WHERE child_id = ?",
            (child_id,),
        ).fetchone()
        return row["parent_id"] if row else None

    def _link_parent(self, parent_id: str, child_id: str) -> None:
        self.store._ensure_connected()
        self.store._tasks_db.execute(
            """CREATE TABLE IF NOT EXISTS task_parent_child (
                parent_id TEXT NOT NULL,
                child_id TEXT NOT NULL,
                PRIMARY KEY (parent_id, child_id)
            )"""
        )
        self.store._tasks_db.execute(
            "INSERT OR IGNORE INTO task_parent_child VALUES (?, ?)",
            (parent_id, child_id),
        )
        self.store._tasks_db.commit()
