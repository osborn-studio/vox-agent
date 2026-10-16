"""Dashboard — unified data aggregation for the UI dashboard view.

Combines data from multiple sources into a single response
optimized for the SwiftUI dashboard rendering.
"""

from __future__ import annotations

import logging
from typing import Any

from .store import StoreManager
from .analytics import TaskAnalytics
from .task_queue_stats import QueueStats
from .health import HealthChecker
from .cost_tracker import CostTracker
from .agent_memory import AgentMemory
from .skills import SkillLoader
from .tools import ToolRegistry
from .heartbeat import HeartbeatMonitor

logger = logging.getLogger(__name__)


class DashboardAggregator:
    """Aggregate all dashboard data into a single response."""

    def __init__(
        self,
        store: StoreManager,
        analytics: TaskAnalytics,
        queue_stats: QueueStats,
        health: HealthChecker,
        costs: CostTracker,
        memory: AgentMemory,
        skills: SkillLoader,
        tools: ToolRegistry,
        heartbeat: HeartbeatMonitor,
    ) -> None:
        self.store = store
        self.analytics = analytics
        self.queue_stats = queue_stats
        self.health = health
        self.costs = costs
        self.memory = memory
        self.skills = skills
        self.tools = tools
        self.heartbeat = heartbeat

    def get_dashboard(self) -> dict[str, Any]:
        """Single API call returns everything the dashboard needs."""
        stats = self.analytics.engine_stats()
        health_report = self.health.check()
        cost_summary = self.costs.summary(days=7)
        queue = self.queue_stats.summary()

        return {
            "health": {
                "status": health_report.status,
                "uptime_seconds": round(health_report.uptime_seconds, 0),
                "warnings": health_report.warnings,
            },
            "tasks": {
                "total": stats.total_tasks,
                "completed": stats.completed,
                "failed": stats.failed,
                "active": stats.active,
                "today": stats.tasks_today,
                "this_week": stats.tasks_this_week,
            },
            "queue": {
                "depth": queue["depth"],
                "throughput": queue["throughput"],
                "avg_wait_seconds": queue["avg_wait_seconds"],
            },
            "agents": [
                {
                    "id": a.agent_id,
                    "tasks": a.total_tasks,
                    "success_rate": round(a.success_rate, 2),
                    "avg_duration": round(a.avg_duration_sec, 1),
                    "online": self.heartbeat.is_online(a.agent_id),
                }
                for a in stats.agents
            ],
            "costs": {
                "week_usd": round(cost_summary.total_cost_usd, 4),
                "today_usd": round(self.costs.today_cost(), 4),
                "over_budget": self.costs.is_over_budget(),
                "by_model": {k: round(v, 4) for k, v in cost_summary.by_model.items()},
            },
            "infrastructure": {
                "tools": len(self.tools.list_tools()),
                "skills": len(self.skills.load_all()),
                "memory_agents": len(self.memory.list_agents_with_memory()),
                "online_agents": len(self.heartbeat.online_agents()),
            },
        }
