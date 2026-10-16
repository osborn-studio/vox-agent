"""Task Tags — categorize tasks with labels for filtering and analytics.

Tags are stored as a comma-separated string in the task table.
Auto-tagging extracts tags from task content using keyword detection.
"""

from __future__ import annotations

import re
import logging

from .store import StoreManager

logger = logging.getLogger(__name__)

# Auto-tag patterns
AUTO_TAGS = {
    "bug": re.compile(r"(bug|fix|error|crash|broken|issue)", re.IGNORECASE),
    "test": re.compile(r"(test|pytest|jest|coverage|spec)", re.IGNORECASE),
    "docs": re.compile(r"(doc|readme|documentation|guide|tutorial)", re.IGNORECASE),
    "refactor": re.compile(r"(refactor|cleanup|reorganize|restructure)", re.IGNORECASE),
    "feature": re.compile(r"(feature|add|new|implement|create|build)", re.IGNORECASE),
    "review": re.compile(r"(review|audit|check|inspect|evaluate)", re.IGNORECASE),
    "deploy": re.compile(r"(deploy|release|ship|publish|staging|production)", re.IGNORECASE),
    "security": re.compile(r"(security|vulnerability|CVE|auth|permission|encrypt)", re.IGNORECASE),
    "performance": re.compile(r"(performance|speed|slow|optimize|benchmark|profil)", re.IGNORECASE),
    "ci": re.compile(r"(CI|CD|pipeline|workflow|github action|jenkins)", re.IGNORECASE),
}


class TagManager:
    """Manage task tags — add, remove, search, auto-tag."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store

    def add_tag(self, task_id: str, tag: str) -> None:
        """Add a tag to a task."""
        self.store._ensure_connected()
        row = self.store._tasks_db.execute(
            "SELECT tags FROM task WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return

        current = set(filter(None, (row["tags"] or "").split(",")))
        current.add(tag.strip().lower())
        self.store._tasks_db.execute(
            "UPDATE task SET tags = ? WHERE id = ?",
            (",".join(sorted(current)), task_id),
        )
        self.store._tasks_db.commit()

    def remove_tag(self, task_id: str, tag: str) -> None:
        """Remove a tag from a task."""
        self.store._ensure_connected()
        row = self.store._tasks_db.execute(
            "SELECT tags FROM task WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return

        current = set(filter(None, (row["tags"] or "").split(",")))
        current.discard(tag.strip().lower())
        self.store._tasks_db.execute(
            "UPDATE task SET tags = ? WHERE id = ?",
            (",".join(sorted(current)), task_id),
        )
        self.store._tasks_db.commit()

    def get_tags(self, task_id: str) -> list[str]:
        """Get tags for a task."""
        self.store._ensure_connected()
        row = self.store._tasks_db.execute(
            "SELECT tags FROM task WHERE id = ?", (task_id,)
        ).fetchone()
        if not row or not row["tags"]:
            return []
        return sorted(filter(None, row["tags"].split(",")))

    def find_by_tag(self, tag: str) -> list[dict]:
        """Find all tasks with a specific tag."""
        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            "SELECT * FROM task WHERE tags LIKE ? ORDER BY created_at DESC",
            (f"%{tag.lower()}%",),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_tags(self) -> dict[str, int]:
        """Get all tags with task counts."""
        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            "SELECT tags FROM task WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()

        counts: dict[str, int] = {}
        for row in rows:
            for tag in filter(None, row["tags"].split(",")):
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def auto_tag(self, task_id: str, text: str) -> list[str]:
        """Auto-detect tags from task content."""
        detected = []
        for tag, pattern in AUTO_TAGS.items():
            if pattern.search(text):
                detected.append(tag)
                self.add_tag(task_id, tag)
        return detected
