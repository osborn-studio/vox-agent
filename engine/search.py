"""Task Search — full-text and filtered search across task history.

Supports:
- Text search across task titles, descriptions, and messages
- Filter by agent, status, priority, date range
- Sort by relevance, date, or priority
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from .store import StoreManager

logger = logging.getLogger(__name__)


@dataclass
class SearchQuery:
    text: str = ""
    agent_id: str | None = None
    status: str | None = None
    priority: int | None = None
    from_date: str | None = None  # ISO format
    to_date: str | None = None
    limit: int = 50
    offset: int = 0
    sort_by: str = "date"  # "date" | "priority" | "relevance"


@dataclass
class SearchResult:
    tasks: list[dict]
    total: int
    query: SearchQuery


class TaskSearch:
    """Search and filter task history."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store

    def search(self, query: SearchQuery) -> SearchResult:
        """Execute a search query against task history."""
        self.store._ensure_connected()

        sql = "SELECT * FROM task WHERE 1=1"
        params: list[Any] = []

        # Text search (title + description + prompt)
        if query.text:
            pattern = f"%{query.text}%"
            sql += " AND (title LIKE ? OR description LIKE ? OR prompt LIKE ?)"
            params.extend([pattern, pattern, pattern])

        # Agent filter
        if query.agent_id:
            sql += " AND agent_id = ?"
            params.append(query.agent_id)

        # Status filter
        if query.status:
            sql += " AND status = ?"
            params.append(query.status)

        # Priority filter
        if query.priority is not None:
            sql += " AND priority >= ?"
            params.append(query.priority)

        # Date range
        if query.from_date:
            sql += " AND created_at >= ?"
            params.append(query.from_date)
        if query.to_date:
            sql += " AND created_at <= ?"
            params.append(query.to_date)

        # Count total
        count_sql = sql.replace("SELECT *", "SELECT COUNT(*)")
        total = self.store._tasks_db.execute(count_sql, params).fetchone()[0]

        # Sort
        if query.sort_by == "priority":
            sql += " ORDER BY priority DESC, created_at DESC"
        elif query.sort_by == "relevance" and query.text:
            # Rough relevance: title match > description match
            sql += " ORDER BY CASE WHEN title LIKE ? THEN 0 ELSE 1 END, created_at DESC"
            params.append(f"%{query.text}%")
        else:
            sql += " ORDER BY created_at DESC"

        # Pagination
        sql += " LIMIT ? OFFSET ?"
        params.extend([query.limit, query.offset])

        rows = self.store._tasks_db.execute(sql, params).fetchall()
        tasks = [dict(r) for r in rows]

        return SearchResult(tasks=tasks, total=total, query=query)

    def search_messages(self, text: str, task_id: str | None = None, limit: int = 50) -> list[dict]:
        """Search within task execution messages."""
        self.store._ensure_connected()

        sql = "SELECT * FROM task_message WHERE content LIKE ?"
        params: list[Any] = [f"%{text}%"]

        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        return [dict(r) for r in self.store._tasks_db.execute(sql, params).fetchall()]

    def recent_activity(self, hours: int = 24, limit: int = 20) -> list[dict]:
        """Get recent task activity within a time window."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        self.store._ensure_connected()

        rows = self.store._tasks_db.execute(
            "SELECT * FROM task WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]
