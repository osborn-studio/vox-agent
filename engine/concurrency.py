"""Concurrency Control — per-agent task limits and global semaphore.

Ensures agents don't exceed their max_concurrent_tasks setting,
and the overall system doesn't spawn too many parallel tasks.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class ConcurrencyController:
    """Manages task execution concurrency at two levels:

    1. Global: max total concurrent tasks across all agents
    2. Per-agent: max concurrent tasks per agent (configurable)

    Usage:
        controller = ConcurrencyController(global_max=10)
        async with controller.acquire("claude-code", max_per_agent=2):
            # execute task
    """

    def __init__(self, global_max: int = 10) -> None:
        self._global_sem = asyncio.Semaphore(global_max)
        self._agent_sems: dict[str, asyncio.Semaphore] = {}
        self._agent_limits: dict[str, int] = {}
        self._active: dict[str, int] = defaultdict(int)
        self._global_max = global_max

    def set_agent_limit(self, agent_id: str, max_concurrent: int) -> None:
        """Set the max concurrent tasks for an agent."""
        self._agent_limits[agent_id] = max_concurrent
        if agent_id not in self._agent_sems:
            self._agent_sems[agent_id] = asyncio.Semaphore(max_concurrent)

    def acquire(self, agent_id: str, max_per_agent: int = 1) -> _ConcurrencyGuard:
        """Return an async context manager that acquires both semaphores."""
        if agent_id not in self._agent_sems:
            self._agent_sems[agent_id] = asyncio.Semaphore(max_per_agent)
        return _ConcurrencyGuard(self, agent_id)

    @property
    def active_count(self) -> int:
        return sum(self._active.values())

    def agent_active_count(self, agent_id: str) -> int:
        return self._active.get(agent_id, 0)

    def status(self) -> dict:
        return {
            "global_max": self._global_max,
            "active_total": self.active_count,
            "per_agent": {
                agent_id: {
                    "active": count,
                    "limit": self._agent_limits.get(agent_id, 1),
                }
                for agent_id, count in self._active.items()
                if count > 0
            },
        }


class _ConcurrencyGuard:
    """Async context manager that acquires global + per-agent semaphores."""

    def __init__(self, controller: ConcurrencyController, agent_id: str) -> None:
        self._ctrl = controller
        self._agent_id = agent_id

    async def __aenter__(self) -> None:
        await self._ctrl._global_sem.acquire()
        agent_sem = self._ctrl._agent_sems[self._agent_id]
        try:
            await agent_sem.acquire()
        except Exception:
            self._ctrl._global_sem.release()
            raise
        self._ctrl._active[self._agent_id] += 1
        logger.debug(
            "acquired: agent=%s active=%d total=%d",
            self._agent_id,
            self._ctrl._active[self._agent_id],
            self._ctrl.active_count,
        )

    async def __aexit__(self, *exc) -> None:
        self._ctrl._active[self._agent_id] -= 1
        self._ctrl._agent_sems[self._agent_id].release()
        self._ctrl._global_sem.release()
        logger.debug(
            "released: agent=%s active=%d total=%d",
            self._agent_id,
            self._ctrl._active[self._agent_id],
            self._ctrl.active_count,
        )
