"""Batch Operations — create and manage multiple tasks at once.

Supports:
- Batch create: create N tasks from a list of prompts
- Batch cancel: cancel all active tasks for an agent
- Batch status: get status of multiple tasks
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .models import Task, TaskPriority
from .scheduler import TaskScheduler
from .dependencies import DependencyManager

logger = logging.getLogger(__name__)


@dataclass
class BatchItem:
    title: str
    prompt: str
    agent_id: str = "claude-code"
    priority: int = 1
    depends_on: list[int] | None = None  # indices into the batch (0-based)


@dataclass
class BatchResult:
    total: int
    created: int
    task_ids: list[str]
    errors: list[str]


class BatchOperations:
    """Execute batch task operations."""

    def __init__(
        self,
        scheduler: TaskScheduler,
        deps: DependencyManager | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.deps = deps

    async def create_batch(
        self,
        items: list[BatchItem],
        provider: str = "claude",
    ) -> BatchResult:
        """Create multiple tasks, optionally with dependencies between them.

        If items have depends_on fields, tasks are created with dependencies
        and only dispatched when their deps complete.
        """
        task_ids: list[str] = []
        errors: list[str] = []

        # First pass: create all tasks (no auto-dispatch if deps exist)
        has_deps = any(item.depends_on for item in items)

        for i, item in enumerate(items):
            try:
                task = await self.scheduler.create_task(
                    agent_id=item.agent_id,
                    title=item.title,
                    prompt=item.prompt,
                    priority=TaskPriority(item.priority),
                    provider=provider,
                    auto_dispatch=not has_deps,  # defer if deps exist
                )
                task_ids.append(task.id)
            except Exception as e:
                task_ids.append("")
                errors.append(f"item {i}: {e}")
                logger.exception("batch item %d failed", i)

        # Second pass: wire up dependencies
        if has_deps and self.deps:
            for i, item in enumerate(items):
                if not item.depends_on or not task_ids[i]:
                    continue
                for dep_idx in item.depends_on:
                    if 0 <= dep_idx < len(task_ids) and task_ids[dep_idx]:
                        self.deps.add_dependency(task_ids[i], task_ids[dep_idx])

            # Dispatch tasks with no dependencies
            for i, item in enumerate(items):
                if not item.depends_on and task_ids[i]:
                    asyncio.create_task(
                        self.scheduler._dispatch(
                            Task(agent_id=item.agent_id, title=item.title,
                                 prompt=item.prompt, id=task_ids[i]),
                            provider,
                        )
                    )

        created = sum(1 for tid in task_ids if tid)
        logger.info("batch: %d/%d tasks created", created, len(items))

        return BatchResult(
            total=len(items),
            created=created,
            task_ids=[tid for tid in task_ids if tid],
            errors=errors,
        )

    async def cancel_agent_tasks(self, agent_id: str) -> int:
        """Cancel all active tasks for an agent. Returns count cancelled."""
        tasks = self.scheduler.store.list_tasks(agent_id=agent_id)
        active = [t for t in tasks if t["status"] in ("draft", "planned", "assigned", "running")]

        cancelled = 0
        for t in active:
            ok = await self.scheduler.cancel_task(t["id"])
            if ok:
                cancelled += 1

        logger.info("cancelled %d/%d active tasks for %s", cancelled, len(active), agent_id)
        return cancelled

    async def batch_status(self, task_ids: list[str]) -> list[dict]:
        """Get status of multiple tasks."""
        results = []
        for tid in task_ids:
            task = self.scheduler.store.get_task(tid)
            if task:
                results.append({
                    "task_id": tid,
                    "status": task["status"],
                    "title": task["title"],
                    "agent_id": task["agent_id"],
                })
            else:
                results.append({"task_id": tid, "status": "not_found"})
        return results
