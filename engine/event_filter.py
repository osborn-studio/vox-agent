"""Event Filter — subscribe to specific event types via WebSocket.

Instead of receiving ALL events, clients can specify which types
they care about. Reduces noise for focused UIs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class EventFilter:
    """Filter events by type for WebSocket clients."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, set[str]] = {}  # client_id → event types

    def subscribe(self, client_id: str, event_types: list[str]) -> None:
        """Subscribe a client to specific event types."""
        self._subscriptions[client_id] = set(event_types)
        logger.debug("client %s subscribed to %s", client_id, event_types)

    def unsubscribe(self, client_id: str) -> None:
        self._subscriptions.pop(client_id, None)

    def should_deliver(self, client_id: str, event_type: str) -> bool:
        """Check if an event should be delivered to a client."""
        sub = self._subscriptions.get(client_id)
        if sub is None:
            return True  # no filter = receive everything
        return event_type in sub or "*" in sub

    def add_type(self, client_id: str, event_type: str) -> None:
        if client_id not in self._subscriptions:
            self._subscriptions[client_id] = set()
        self._subscriptions[client_id].add(event_type)

    def remove_type(self, client_id: str, event_type: str) -> None:
        if client_id in self._subscriptions:
            self._subscriptions[client_id].discard(event_type)

    def get_subscription(self, client_id: str) -> list[str]:
        sub = self._subscriptions.get(client_id)
        return sorted(sub) if sub else ["*"]

    def active_clients(self) -> int:
        return len(self._subscriptions)


# Pre-defined filter presets
FILTER_PRESETS = {
    "tasks_only": ["task:created", "task:dispatched", "task:progress",
                    "task:completed", "task:failed", "task:cancelled"],
    "messages_only": ["task:message"],
    "status_only": ["agent:status", "task:completed", "task:failed"],
    "notifications": ["notification"],
    "all": ["*"],
}
