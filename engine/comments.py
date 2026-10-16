"""Task Comments — add notes, feedback, and annotations to tasks.

Users can comment on task results, agents can add explanations,
and the system can add status notes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from .store import StoreManager

logger = logging.getLogger(__name__)


@dataclass
class Comment:
    id: str
    task_id: str
    author_type: str  # "user" | "agent" | "system"
    author_id: str
    content: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CommentManager:
    """Manage comments on tasks."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store

    def add(self, task_id: str, content: str, author_type: str = "user",
            author_id: str = "gilbert") -> Comment:
        comment = Comment(
            id=uuid4().hex[:12],
            task_id=task_id,
            author_type=author_type,
            author_id=author_id,
            content=content,
        )
        self.store._ensure_connected()
        self.store._tasks_db.execute(
            """INSERT INTO task_comment (id, task_id, author_type, author_id, content, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (comment.id, comment.task_id, comment.author_type,
             comment.author_id, comment.content, comment.created_at),
        )
        self.store._tasks_db.commit()
        return comment

    def list_for_task(self, task_id: str) -> list[Comment]:
        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            "SELECT * FROM task_comment WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [Comment(**dict(r)) for r in rows]

    def delete(self, comment_id: str) -> bool:
        self.store._ensure_connected()
        cursor = self.store._tasks_db.execute(
            "DELETE FROM task_comment WHERE id = ?", (comment_id,),
        )
        self.store._tasks_db.commit()
        return cursor.rowcount > 0

    def count_for_task(self, task_id: str) -> int:
        self.store._ensure_connected()
        row = self.store._tasks_db.execute(
            "SELECT COUNT(*) FROM task_comment WHERE task_id = ?", (task_id,),
        ).fetchone()
        return row[0]
