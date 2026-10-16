"""Agent Heartbeat — track agent availability and health.

Each agent reports its status periodically. If an agent goes silent,
it's marked as offline after a configurable timeout.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .events import EventBus

logger = logging.getLogger(__name__)


@dataclass
class AgentHeartbeat:
    agent_id: str
    status: str  # "online" | "working" | "idle" | "offline"
    last_seen: float  # time.time()
    active_tasks: int = 0
    metadata: dict[str, Any] | None = None


class HeartbeatMonitor:
    """Track agent liveness via heartbeat signals.

    Agents call report() periodically. The monitor checks for stale
    agents and emits agent:status events when status changes.
    """

    def __init__(
        self,
        event_bus: EventBus,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.event_bus = event_bus
        self.timeout = timeout_seconds
        self._agents: dict[str, AgentHeartbeat] = {}

    async def report(
        self,
        agent_id: str,
        status: str = "online",
        active_tasks: int = 0,
        metadata: dict | None = None,
    ) -> None:
        """Record a heartbeat from an agent."""
        prev = self._agents.get(agent_id)
        prev_status = prev.status if prev else None

        self._agents[agent_id] = AgentHeartbeat(
            agent_id=agent_id,
            status=status,
            last_seen=time.time(),
            active_tasks=active_tasks,
            metadata=metadata,
        )

        # Emit status change event
        if prev_status != status:
            await self.event_bus.emit("agent:status", {
                "agent_id": agent_id,
                "status": status,
                "previous": prev_status,
                "active_tasks": active_tasks,
            })
            logger.info("agent %s: %s → %s", agent_id, prev_status, status)

    async def check_stale(self) -> list[str]:
        """Check for agents that haven't reported. Returns newly-offline IDs."""
        now = time.time()
        stale = []

        for agent_id, hb in list(self._agents.items()):
            if hb.status == "offline":
                continue
            if now - hb.last_seen > self.timeout:
                await self.report(agent_id, status="offline")
                stale.append(agent_id)

        return stale

    def get_status(self, agent_id: str) -> AgentHeartbeat | None:
        return self._agents.get(agent_id)

    def all_statuses(self) -> list[AgentHeartbeat]:
        return list(self._agents.values())

    def online_agents(self) -> list[str]:
        return [
            hb.agent_id for hb in self._agents.values()
            if hb.status in ("online", "working", "idle")
        ]

    def is_online(self, agent_id: str) -> bool:
        hb = self._agents.get(agent_id)
        return hb is not None and hb.status != "offline"
