"""Notifications — alert the user about important task events.

Sends notifications via the Event Bus which SwiftUI can render as:
- macOS system notifications (NSUserNotification)
- In-app toast messages
- TaskBar updates
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .events import EventBus

logger = logging.getLogger(__name__)


class NotificationLevel(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Notification:
    title: str
    message: str
    level: NotificationLevel = NotificationLevel.INFO
    task_id: str | None = None
    agent_id: str | None = None
    action_url: str | None = None  # deep link for SwiftUI to navigate


class NotificationService:
    """Sends notifications through the Event Bus.

    SwiftUI's EngineEventHandler listens for 'notification' events
    and renders them appropriately.
    """

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self._setup_auto_notifications()

    def _setup_auto_notifications(self) -> None:
        """Register listeners for automatic notifications on key events."""
        self.event_bus.on("task:completed", self._on_task_completed)
        self.event_bus.on("task:failed", self._on_task_failed)

    async def send(self, notification: Notification) -> None:
        """Send a notification through the event bus."""
        await self.event_bus.emit("notification", {
            "title": notification.title,
            "message": notification.message,
            "level": notification.level.value,
            "task_id": notification.task_id,
            "agent_id": notification.agent_id,
            "action_url": notification.action_url,
        })
        logger.info("notification: [%s] %s", notification.level.value, notification.title)

    async def _on_task_completed(self, event_type: str, payload: Any) -> None:
        task_id = payload.get("task_id", "") if isinstance(payload, dict) else ""
        await self.send(Notification(
            title="Task Completed",
            message=f"Task {task_id[:8]} finished successfully.",
            level=NotificationLevel.SUCCESS,
            task_id=task_id,
        ))

    async def _on_task_failed(self, event_type: str, payload: Any) -> None:
        task_id = payload.get("task_id", "") if isinstance(payload, dict) else ""
        error = payload.get("error", "") if isinstance(payload, dict) else ""
        await self.send(Notification(
            title="Task Failed",
            message=f"Task {task_id[:8]} failed: {error[:100]}",
            level=NotificationLevel.ERROR,
            task_id=task_id,
        ))
