"""Event Bus — in-memory event dispatch (inspired by Multica)."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

Handler = Callable[[str, Any], Awaitable[None]]


@dataclass
class Event:
    type: str
    payload: Any
    actor_type: str = "system"
    actor_id: str = ""


class EventBus:
    """Central event dispatcher.

    Listeners register by event type. When an event is emitted it is
    delivered to all matching listeners **and** pushed to every connected
    WebSocket client.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[Handler]] = defaultdict(list)
        self._ws_queues: list[asyncio.Queue[dict]] = []

    # -- listener registration -----------------------------------------------

    def on(self, event_type: str, handler: Handler) -> None:
        self._listeners[event_type].append(handler)

    def on_all(self, handler: Handler) -> None:
        """Register a handler that receives every event."""
        self._listeners["*"].append(handler)

    # -- emit ----------------------------------------------------------------

    async def emit(self, event_type: str, payload: Any, **kwargs: Any) -> None:
        event = Event(type=event_type, payload=payload, **kwargs)

        # notify typed listeners
        for handler in self._listeners.get(event_type, []):
            try:
                await handler(event_type, payload)
            except Exception:
                logger.exception("event handler error for %s", event_type)

        # notify wildcard listeners
        for handler in self._listeners.get("*", []):
            try:
                await handler(event_type, payload)
            except Exception:
                logger.exception("wildcard handler error for %s", event_type)

        # push to WebSocket clients
        message = self._serialize(event)
        for q in list(self._ws_queues):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("ws queue full, dropping event %s", event_type)

    # -- WebSocket subscription ----------------------------------------------

    def subscribe_ws(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self._ws_queues.append(q)
        return q

    def unsubscribe_ws(self, q: asyncio.Queue[dict]) -> None:
        try:
            self._ws_queues.remove(q)
        except ValueError:
            pass

    # -- serialisation -------------------------------------------------------

    @staticmethod
    def _serialize(event: Event) -> dict:
        payload = event.payload
        if hasattr(payload, "__dataclass_fields__"):
            payload = asdict(payload)
        return {"type": event.type, "payload": payload}
