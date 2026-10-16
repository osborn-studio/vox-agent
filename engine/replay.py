"""Event Replay — replay past task executions for debugging.

Re-emits stored task messages through the Event Bus,
allowing the SwiftUI UI to "play back" a completed task
as if it were happening in real-time.
"""

from __future__ import annotations

import asyncio
import logging

from .store import StoreManager
from .events import EventBus

logger = logging.getLogger(__name__)


class EventReplay:
    """Replay stored task execution events."""

    def __init__(self, store: StoreManager, event_bus: EventBus) -> None:
        self.store = store
        self.event_bus = event_bus

    async def replay(
        self,
        task_id: str,
        speed: float = 1.0,
        delay_between_ms: int = 200,
    ) -> int:
        """Replay a task's execution messages through the Event Bus.

        Args:
            task_id: Task to replay
            speed: Playback speed (2.0 = 2x faster)
            delay_between_ms: Base delay between messages

        Returns:
            Number of messages replayed
        """
        msgs = self.store.list_task_messages(task_id)
        if not msgs:
            return 0

        await self.event_bus.emit("replay:started", {
            "task_id": task_id,
            "message_count": len(msgs),
            "speed": speed,
        })

        actual_delay = delay_between_ms / 1000.0 / speed
        count = 0

        for msg in msgs:
            await self.event_bus.emit("task:message", {
                "task_id": task_id,
                "seq": msg["seq"],
                "author_type": msg["author_type"],
                "author_id": msg["author_id"],
                "type": msg["type"],
                "content": msg.get("content", ""),
                "tool": msg.get("tool", ""),
                "output": msg.get("output", ""),
                "replay": True,
            })
            count += 1
            await asyncio.sleep(actual_delay)

        await self.event_bus.emit("replay:completed", {
            "task_id": task_id,
            "messages_replayed": count,
        })

        logger.info("replayed %d messages for task %s at %.1fx", count, task_id[:8], speed)
        return count

    async def replay_summary(self, task_id: str) -> dict | None:
        """Get a summary of what would be replayed."""
        task = self.store.get_task(task_id)
        if not task:
            return None

        msgs = self.store.list_task_messages(task_id)
        types = {}
        for m in msgs:
            t = m["type"]
            types[t] = types.get(t, 0) + 1

        return {
            "task_id": task_id,
            "title": task["title"],
            "status": task["status"],
            "message_count": len(msgs),
            "message_types": types,
        }
