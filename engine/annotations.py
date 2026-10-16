"""Task Annotations — structured metadata attached to tasks.

Unlike comments (free text), annotations are key-value pairs:
- "review_score": "8/10"
- "test_coverage": "87%"
- "deploy_target": "staging"
- "estimated_time": "30min"

Used for filtering, reporting, and SwiftUI display.
"""

from __future__ import annotations

import logging

from .store import StoreManager

logger = logging.getLogger(__name__)


class AnnotationManager:
    """Manage structured task annotations (key-value metadata)."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store

    def set(self, task_id: str, key: str, value: str) -> None:
        """Set an annotation on a task (upsert)."""
        self.store._ensure_connected()
        self.store._tasks_db.execute(
            """INSERT INTO task_annotation (task_id, key, value)
               VALUES (?, ?, ?)
               ON CONFLICT(task_id, key) DO UPDATE SET value = ?""",
            (task_id, key, value, value),
        )
        self.store._tasks_db.commit()

    def get(self, task_id: str, key: str) -> str | None:
        """Get a specific annotation value."""
        self.store._ensure_connected()
        row = self.store._tasks_db.execute(
            "SELECT value FROM task_annotation WHERE task_id = ? AND key = ?",
            (task_id, key),
        ).fetchone()
        return row["value"] if row else None

    def get_all(self, task_id: str) -> dict[str, str]:
        """Get all annotations for a task."""
        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            "SELECT key, value FROM task_annotation WHERE task_id = ? ORDER BY key",
            (task_id,),
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def delete(self, task_id: str, key: str) -> bool:
        """Remove a specific annotation."""
        self.store._ensure_connected()
        cursor = self.store._tasks_db.execute(
            "DELETE FROM task_annotation WHERE task_id = ? AND key = ?",
            (task_id, key),
        )
        self.store._tasks_db.commit()
        return cursor.rowcount > 0

    def delete_all(self, task_id: str) -> int:
        """Remove all annotations for a task."""
        self.store._ensure_connected()
        cursor = self.store._tasks_db.execute(
            "DELETE FROM task_annotation WHERE task_id = ?", (task_id,),
        )
        self.store._tasks_db.commit()
        return cursor.rowcount

    def find_by_annotation(self, key: str, value: str | None = None) -> list[str]:
        """Find task IDs with a specific annotation key (and optionally value)."""
        self.store._ensure_connected()
        if value:
            rows = self.store._tasks_db.execute(
                "SELECT task_id FROM task_annotation WHERE key = ? AND value = ?",
                (key, value),
            ).fetchall()
        else:
            rows = self.store._tasks_db.execute(
                "SELECT task_id FROM task_annotation WHERE key = ?", (key,),
            ).fetchall()
        return [r["task_id"] for r in rows]
