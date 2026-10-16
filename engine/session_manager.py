"""Session Manager — track user interaction sessions.

A session starts when the user opens VoxLog and ends when they close it.
Each session groups all tasks, messages, and agent interactions
for that sitting. Enables "what did I do today?" queries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any

from .store import StoreManager
from .events import EventBus

logger = logging.getLogger(__name__)


@dataclass
class Session:
    id: str
    started_at: str
    ended_at: str = ""
    task_count: int = 0
    message_count: int = 0
    agents_used: list[str] = field(default_factory=list)
    duration_seconds: float = 0


class SessionManager:
    """Track user interaction sessions."""

    def __init__(self, store: StoreManager, event_bus: EventBus) -> None:
        self.store = store
        self.event_bus = event_bus
        self._current: Session | None = None
        self._task_counter = 0
        self._message_counter = 0
        self._agents: set[str] = set()

        # Listen for events to track session activity
        event_bus.on("task:created", self._on_task)
        event_bus.on("task:message", self._on_message)

    def start(self) -> Session:
        """Start a new session."""
        if self._current:
            self.end()  # end previous session first

        self._current = Session(
            id=uuid4().hex[:12],
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._task_counter = 0
        self._message_counter = 0
        self._agents.clear()

        logger.info("session started: %s", self._current.id)
        return self._current

    def end(self) -> Session | None:
        """End the current session."""
        if not self._current:
            return None

        now = datetime.now(timezone.utc)
        started = datetime.fromisoformat(self._current.started_at)
        self._current.ended_at = now.isoformat()
        self._current.duration_seconds = (now - started).total_seconds()
        self._current.task_count = self._task_counter
        self._current.message_count = self._message_counter
        self._current.agents_used = sorted(self._agents)

        session = self._current
        self._current = None

        logger.info(
            "session ended: %s (%.0fs, %d tasks, %d messages)",
            session.id, session.duration_seconds,
            session.task_count, session.message_count,
        )

        # Store session record in activity log
        from .models import ActivityEntry
        self.store.insert_activity(ActivityEntry(
            actor_type="system",
            actor_id="session_manager",
            action="session:ended",
            details={
                "session_id": session.id,
                "duration_seconds": session.duration_seconds,
                "task_count": session.task_count,
                "message_count": session.message_count,
                "agents_used": session.agents_used,
            },
        ))
        return session

    @property
    def current(self) -> Session | None:
        return self._current

    @property
    def is_active(self) -> bool:
        return self._current is not None

    async def _on_task(self, event_type: str, payload: Any) -> None:
        if self._current:
            self._task_counter += 1
            if isinstance(payload, dict):
                agent = payload.get("agent_id", "")
                if agent:
                    self._agents.add(agent)
            elif hasattr(payload, "agent_id"):
                self._agents.add(payload.agent_id)

    async def _on_message(self, event_type: str, payload: Any) -> None:
        if self._current:
            self._message_counter += 1
