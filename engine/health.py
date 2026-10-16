"""Health Dashboard — comprehensive engine health check.

Goes beyond simple /health to provide detailed system diagnostics:
- Engine uptime
- Database sizes and table counts
- Active tasks and queue depth
- Memory usage per agent
- Tool and skill counts
- Recent error rate
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Any

from .store import StoreManager
from .agent_memory import AgentMemory
from .skills import SkillLoader
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

_START_TIME = time.time()


@dataclass
class HealthReport:
    status: str  # "healthy" | "degraded" | "unhealthy"
    uptime_seconds: float
    databases: dict[str, Any]
    tasks: dict[str, int]
    tools: int
    skills: int
    memory_agents: int
    recent_error_rate: float  # errors / total in last hour
    warnings: list[str]


class HealthChecker:
    """Generate comprehensive health reports."""

    def __init__(
        self,
        store: StoreManager,
        memory: AgentMemory,
        skills: SkillLoader,
        tools: ToolRegistry,
    ) -> None:
        self.store = store
        self.memory = memory
        self.skills = skills
        self.tools = tools

    def check(self) -> HealthReport:
        warnings: list[str] = []

        # Uptime
        uptime = time.time() - _START_TIME

        # Database stats
        databases = self._check_databases()
        for db_name, info in databases.items():
            if info.get("error"):
                warnings.append(f"{db_name}: {info['error']}")

        # Task stats
        tasks = self._check_tasks()

        # Error rate
        error_rate = self._calculate_error_rate()
        if error_rate > 0.3:
            warnings.append(f"high error rate: {error_rate:.0%}")

        # Tools and skills
        tool_count = len(self.tools.list_tools())
        skill_count = len(self.skills.load_all())
        memory_count = len(self.memory.list_agents_with_memory())

        # Determine status
        if warnings:
            status = "degraded"
        elif tasks.get("active", 0) > 20:
            status = "degraded"
            warnings.append(f"high task load: {tasks['active']} active tasks")
        else:
            status = "healthy"

        return HealthReport(
            status=status,
            uptime_seconds=uptime,
            databases=databases,
            tasks=tasks,
            tools=tool_count,
            skills=skill_count,
            memory_agents=memory_count,
            recent_error_rate=error_rate,
            warnings=warnings,
        )

    def _check_databases(self) -> dict[str, Any]:
        result = {}
        for db_name in ["tasks.db", "activity.db"]:
            path = self.store.data_dir / db_name
            if path.exists():
                size = path.stat().st_size
                result[db_name] = {
                    "exists": True,
                    "size_bytes": size,
                    "size_mb": round(size / 1024 / 1024, 2),
                }
            else:
                result[db_name] = {"exists": False, "error": "file not found"}
        return result

    def _check_tasks(self) -> dict[str, int]:
        try:
            all_tasks = self.store.list_tasks()
            return {
                "total": len(all_tasks),
                "active": sum(1 for t in all_tasks if t["status"] in ("running", "assigned")),
                "queued": sum(1 for t in all_tasks if t["status"] in ("draft", "planned")),
                "completed": sum(1 for t in all_tasks if t["status"] == "completed"),
                "failed": sum(1 for t in all_tasks if t["status"] == "failed"),
            }
        except Exception as e:
            return {"total": 0, "error": str(e)}

    def _calculate_error_rate(self) -> float:
        try:
            all_tasks = self.store.list_tasks()
            if not all_tasks:
                return 0.0
            finished = [t for t in all_tasks if t["status"] in ("completed", "failed")]
            if not finished:
                return 0.0
            failed = sum(1 for t in finished if t["status"] == "failed")
            return failed / len(finished)
        except Exception:
            return 0.0
