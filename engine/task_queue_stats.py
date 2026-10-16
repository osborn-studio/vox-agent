"""Task Queue Stats — real-time queue depth and throughput metrics.

Provides data for monitoring dashboards:
- Queue depth by status
- Throughput (tasks/minute, tasks/hour)
- Agent utilization percentage
- Average wait time in queue
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from .store import StoreManager

logger = logging.getLogger(__name__)


class QueueStats:
    """Real-time task queue statistics."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store

    def queue_depth(self) -> dict[str, int]:
        """Current task count by status."""
        tasks = self.store.list_tasks()
        counts: dict[str, int] = {}
        for t in tasks:
            status = t["status"]
            counts[status] = counts.get(status, 0) + 1
        return counts

    def throughput(self, minutes: int = 60) -> dict[str, float]:
        """Tasks completed per minute over the last N minutes."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        tasks = self.store.list_tasks()
        recent_completed = [
            t for t in tasks
            if t["status"] == "completed" and (t.get("completed_at") or "") >= cutoff
        ]

        per_minute = len(recent_completed) / max(minutes, 1)
        per_hour = per_minute * 60

        return {
            "tasks_completed": len(recent_completed),
            "window_minutes": minutes,
            "per_minute": round(per_minute, 2),
            "per_hour": round(per_hour, 1),
        }

    def agent_utilization(self) -> list[dict]:
        """Agent utilization: active tasks / max capacity."""
        tasks = self.store.list_tasks()
        agents: dict[str, dict] = {}

        for t in tasks:
            aid = t["agent_id"]
            if aid not in agents:
                agents[aid] = {"total": 0, "active": 0, "completed": 0, "failed": 0}
            agents[aid]["total"] += 1
            if t["status"] in ("running", "assigned"):
                agents[aid]["active"] += 1
            elif t["status"] == "completed":
                agents[aid]["completed"] += 1
            elif t["status"] == "failed":
                agents[aid]["failed"] += 1

        return [
            {
                "agent_id": aid,
                "active": data["active"],
                "total": data["total"],
                "completed": data["completed"],
                "failed": data["failed"],
                "utilization": round(data["active"] / max(data["total"], 1), 2),
            }
            for aid, data in sorted(agents.items())
        ]

    def avg_wait_time(self) -> float:
        """Average time tasks spend in queue before starting (seconds)."""
        tasks = self.store.list_tasks()
        wait_times = []

        for t in tasks:
            created = t.get("created_at", "")
            started = t.get("started_at", "")
            if created and started:
                try:
                    c = datetime.fromisoformat(created)
                    s = datetime.fromisoformat(started)
                    wait_times.append((s - c).total_seconds())
                except (ValueError, TypeError):
                    pass

        return round(sum(wait_times) / len(wait_times), 1) if wait_times else 0.0

    def summary(self) -> dict:
        """Full queue summary."""
        return {
            "depth": self.queue_depth(),
            "throughput": self.throughput(),
            "utilization": self.agent_utilization(),
            "avg_wait_seconds": self.avg_wait_time(),
        }
