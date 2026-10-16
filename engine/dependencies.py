"""Task Dependencies — define execution order constraints between tasks.

Enables complex multi-step workflows:
  Task A (code review) → Task B (fix issues) → Task C (re-test)

When a task with dependencies is created, it stays in 'planned' status
until all dependencies complete. The scheduler checks deps before dispatch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .store import StoreManager

logger = logging.getLogger(__name__)


@dataclass
class TaskDependency:
    task_id: str
    depends_on: str  # task_id this depends on
    status: str = "pending"  # "pending" | "satisfied" | "failed"


class DependencyManager:
    """Track and resolve task dependencies.

    Dependencies are stored in the tasks.db as a separate table.
    """

    def __init__(self, store: StoreManager) -> None:
        self.store = store

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        """Add a dependency: task_id depends on depends_on completing first."""
        self.store._ensure_connected()
        self.store._tasks_db.execute(
            "INSERT OR IGNORE INTO task_dependency (task_id, depends_on, status) VALUES (?, ?, 'pending')",
            (task_id, depends_on),
        )
        self.store._tasks_db.commit()
        logger.info("dependency: %s depends on %s", task_id, depends_on)

    def get_dependencies(self, task_id: str) -> list[TaskDependency]:
        """Get all dependencies for a task."""
        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            "SELECT task_id, depends_on, status FROM task_dependency WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        return [TaskDependency(**dict(r)) for r in rows]

    def get_dependents(self, task_id: str) -> list[str]:
        """Get all tasks that depend on this task."""
        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            "SELECT task_id FROM task_dependency WHERE depends_on = ?",
            (task_id,),
        ).fetchall()
        return [r["task_id"] for r in rows]

    def are_deps_satisfied(self, task_id: str) -> bool:
        """Check if all dependencies for a task are completed."""
        deps = self.get_dependencies(task_id)
        if not deps:
            return True

        for dep in deps:
            task = self.store.get_task(dep.depends_on)
            if not task or task["status"] != "completed":
                return False
        return True

    def on_task_completed(self, completed_task_id: str) -> list[str]:
        """Called when a task completes. Returns task IDs now ready to dispatch.

        Updates dependency status and checks if any dependent tasks
        have all their deps satisfied.
        """
        self.store._ensure_connected()

        # Mark this dep as satisfied
        self.store._tasks_db.execute(
            "UPDATE task_dependency SET status = 'satisfied' WHERE depends_on = ?",
            (completed_task_id,),
        )
        self.store._tasks_db.commit()

        # Find tasks that depend on the completed task
        dependents = self.get_dependents(completed_task_id)
        ready = []
        for dep_task_id in dependents:
            if self.are_deps_satisfied(dep_task_id):
                ready.append(dep_task_id)
                logger.info("task %s now ready (all deps satisfied)", dep_task_id)

        return ready

    def on_task_failed(self, failed_task_id: str) -> list[str]:
        """Called when a task fails. Returns task IDs that can never complete."""
        self.store._ensure_connected()

        self.store._tasks_db.execute(
            "UPDATE task_dependency SET status = 'failed' WHERE depends_on = ?",
            (failed_task_id,),
        )
        self.store._tasks_db.commit()

        return self.get_dependents(failed_task_id)
