"""Task History — queryable timeline of all task state transitions.

Provides a complete audit of every status change for every task,
enabling "what happened to task X?" debugging and replay.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .store import StoreManager

logger = logging.getLogger(__name__)


@dataclass
class StateTransition:
    task_id: str
    from_status: str
    to_status: str
    timestamp: str
    actor: str = "system"
    reason: str = ""


class TaskHistory:
    """Track and query task state transitions."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store

    def record(self, task_id: str, from_status: str, to_status: str,
               actor: str = "system", reason: str = "") -> None:
        self.store._ensure_connected()
        self.store._tasks_db.execute(
            """INSERT INTO task_history
               (task_id, from_status, to_status, timestamp, actor, reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, from_status, to_status,
             datetime.now(timezone.utc).isoformat(), actor, reason),
        )
        self.store._tasks_db.commit()

    def get_transitions(self, task_id: str) -> list[StateTransition]:
        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            "SELECT * FROM task_history WHERE task_id = ? ORDER BY timestamp",
            (task_id,),
        ).fetchall()
        return [StateTransition(**dict(r)) for r in rows]

    def get_timeline(self, task_id: str) -> list[dict]:
        """Human-readable timeline."""
        transitions = self.get_transitions(task_id)
        return [
            {
                "time": t.timestamp[-12:],
                "change": f"{t.from_status} → {t.to_status}",
                "actor": t.actor,
                "reason": t.reason,
            }
            for t in transitions
        ]

    def time_in_status(self, task_id: str) -> dict[str, float]:
        """Calculate time spent in each status (seconds)."""
        transitions = self.get_transitions(task_id)
        if not transitions:
            return {}

        durations: dict[str, float] = {}
        for i, t in enumerate(transitions):
            ts = datetime.fromisoformat(t.timestamp)
            if i + 1 < len(transitions):
                next_ts = datetime.fromisoformat(transitions[i + 1].timestamp)
                dur = (next_ts - ts).total_seconds()
            else:
                dur = (datetime.now(timezone.utc) - ts).total_seconds()
            durations[t.to_status] = durations.get(t.to_status, 0) + dur

        return {k: round(v, 1) for k, v in durations.items()}

    def recent_transitions(self, limit: int = 50) -> list[StateTransition]:
        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            "SELECT * FROM task_history ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [StateTransition(**dict(r)) for r in rows]
