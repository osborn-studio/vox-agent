"""Favorites — bookmark important tasks for quick access.

Users can star tasks they want to revisit. Favorites persist
across sessions and appear in a dedicated filtered view.
"""

from __future__ import annotations

import logging

from .store import StoreManager

logger = logging.getLogger(__name__)


class FavoritesManager:
    """Manage task bookmarks."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store

    def toggle(self, task_id: str) -> bool:
        """Toggle favorite status. Returns new state."""
        self.store._ensure_connected()
        row = self.store._tasks_db.execute(
            "SELECT favorite FROM task WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return False
        new_val = 0 if row["favorite"] else 1
        self.store._tasks_db.execute(
            "UPDATE task SET favorite = ? WHERE id = ?", (new_val, task_id)
        )
        self.store._tasks_db.commit()
        return bool(new_val)

    def set(self, task_id: str, favorite: bool) -> None:
        self.store._ensure_connected()
        self.store._tasks_db.execute(
            "UPDATE task SET favorite = ? WHERE id = ?", (1 if favorite else 0, task_id)
        )
        self.store._tasks_db.commit()

    def is_favorite(self, task_id: str) -> bool:
        self.store._ensure_connected()
        row = self.store._tasks_db.execute(
            "SELECT favorite FROM task WHERE id = ?", (task_id,)
        ).fetchone()
        return bool(row["favorite"]) if row else False

    def list_favorites(self) -> list[dict]:
        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            "SELECT * FROM task WHERE favorite = 1 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        self.store._ensure_connected()
        row = self.store._tasks_db.execute(
            "SELECT COUNT(*) FROM task WHERE favorite = 1"
        ).fetchone()
        return row[0]
