"""Task Diff — compare outputs between task runs.

When the same task is re-run, shows what changed in the output.
Useful for reviewing code review results, test runs, etc.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass

from .store import StoreManager

logger = logging.getLogger(__name__)


@dataclass
class DiffResult:
    task_a_id: str
    task_b_id: str
    added_lines: int
    removed_lines: int
    unchanged_lines: int
    diff_text: str
    similarity: float  # 0.0-1.0


class TaskDiff:
    """Compare outputs between task executions."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store

    def compare_tasks(self, task_a_id: str, task_b_id: str) -> DiffResult | None:
        """Compare the text output of two tasks."""
        text_a = self._get_task_output(task_a_id)
        text_b = self._get_task_output(task_b_id)

        if text_a is None or text_b is None:
            return None

        return self._compute_diff(task_a_id, task_b_id, text_a, text_b)

    def compare_latest_runs(self, agent_id: str, title_contains: str = "") -> DiffResult | None:
        """Compare the two most recent completed tasks for an agent."""
        tasks = self.store.list_tasks(agent_id=agent_id)
        completed = [t for t in tasks if t["status"] == "completed"]

        if title_contains:
            completed = [t for t in completed if title_contains.lower() in t["title"].lower()]

        if len(completed) < 2:
            return None

        return self.compare_tasks(completed[0]["id"], completed[1]["id"])

    def _get_task_output(self, task_id: str) -> str | None:
        """Extract text output from a task's messages."""
        msgs = self.store.list_task_messages(task_id)
        if not msgs:
            return None
        text_msgs = [m["content"] for m in msgs if m["type"] == "text" and m.get("content")]
        return "\n".join(text_msgs) if text_msgs else None

    def _compute_diff(self, id_a: str, id_b: str, text_a: str, text_b: str) -> DiffResult:
        lines_a = text_a.splitlines(keepends=True)
        lines_b = text_b.splitlines(keepends=True)

        diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=f"task-{id_a[:8]}", tofile=f"task-{id_b[:8]}"))
        diff_text = "".join(diff)

        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

        similarity = difflib.SequenceMatcher(None, text_a, text_b).ratio()

        return DiffResult(
            task_a_id=id_a,
            task_b_id=id_b,
            added_lines=added,
            removed_lines=removed,
            unchanged_lines=max(len(lines_a), len(lines_b)) - max(added, removed),
            diff_text=diff_text,
            similarity=round(similarity, 3),
        )
