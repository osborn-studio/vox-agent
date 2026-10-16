"""Analytics — task execution statistics and agent performance metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from .store import StoreManager


@dataclass
class AgentStats:
    agent_id: str
    total_tasks: int
    completed: int
    failed: int
    cancelled: int
    active: int
    avg_duration_sec: float
    success_rate: float  # 0.0-1.0


@dataclass
class EngineStats:
    total_tasks: int
    completed: int
    failed: int
    cancelled: int
    active: int
    agents: list[AgentStats]
    tasks_today: int
    tasks_this_week: int


class TaskAnalytics:
    """Compute task execution statistics."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store

    def engine_stats(self) -> EngineStats:
        """Overall engine performance stats."""
        all_tasks = self.store.list_tasks()

        completed = [t for t in all_tasks if t["status"] == "completed"]
        failed = [t for t in all_tasks if t["status"] == "failed"]
        cancelled = [t for t in all_tasks if t["status"] == "cancelled"]
        active = [t for t in all_tasks if t["status"] in ("running", "assigned")]

        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        week_ago = (now - timedelta(days=7)).isoformat()

        tasks_today = sum(1 for t in all_tasks if t["created_at"].startswith(today))
        tasks_week = sum(1 for t in all_tasks if t["created_at"] >= week_ago)

        # Per-agent stats
        agent_ids = {t["agent_id"] for t in all_tasks}
        agents = [self.agent_stats(aid) for aid in sorted(agent_ids)]

        return EngineStats(
            total_tasks=len(all_tasks),
            completed=len(completed),
            failed=len(failed),
            cancelled=len(cancelled),
            active=len(active),
            agents=agents,
            tasks_today=tasks_today,
            tasks_this_week=tasks_week,
        )

    def agent_stats(self, agent_id: str) -> AgentStats:
        """Stats for a specific agent."""
        tasks = self.store.list_tasks(agent_id=agent_id)
        completed = [t for t in tasks if t["status"] == "completed"]
        failed = [t for t in tasks if t["status"] == "failed"]
        cancelled = [t for t in tasks if t["status"] == "cancelled"]
        active = [t for t in tasks if t["status"] in ("running", "assigned")]

        # Average duration for completed tasks
        durations = []
        for t in completed:
            if t.get("started_at") and t.get("completed_at"):
                try:
                    start = datetime.fromisoformat(t["started_at"])
                    end = datetime.fromisoformat(t["completed_at"])
                    durations.append((end - start).total_seconds())
                except (ValueError, TypeError):
                    pass

        avg_dur = sum(durations) / len(durations) if durations else 0.0
        finished = len(completed) + len(failed)
        success_rate = len(completed) / finished if finished > 0 else 0.0

        return AgentStats(
            agent_id=agent_id,
            total_tasks=len(tasks),
            completed=len(completed),
            failed=len(failed),
            cancelled=len(cancelled),
            active=len(active),
            avg_duration_sec=avg_dur,
            success_rate=success_rate,
        )
