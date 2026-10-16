"""Priority Queue — dispatch tasks by priority order.

Urgent tasks jump ahead of medium/low tasks in the queue.
Supports priority boosting for tasks that have waited too long.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from heapq import heappush, heappop

logger = logging.getLogger(__name__)


@dataclass(order=True)
class PriorityItem:
    """Heap item: lower number = higher priority."""
    sort_key: tuple = field(compare=True)
    task_id: str = field(compare=False)
    agent_id: str = field(compare=False)
    prompt: str = field(compare=False)
    provider: str = field(compare=False)
    enqueued_at: float = field(compare=False, default_factory=time.time)

    @classmethod
    def create(
        cls, task_id: str, agent_id: str, prompt: str,
        priority: int = 1, provider: str = "claude",
    ) -> PriorityItem:
        # Negate priority so higher priority number = lower sort key
        return cls(
            sort_key=(-priority, time.time()),
            task_id=task_id,
            agent_id=agent_id,
            prompt=prompt,
            provider=provider,
        )


class PriorityTaskQueue:
    """Priority-ordered task dispatch queue.

    Tasks are dispatched in priority order (urgent > high > medium > low).
    Within same priority, FIFO order is maintained.
    """

    def __init__(self) -> None:
        self._heap: list[PriorityItem] = []
        self._event = asyncio.Event()
        self._size = 0

    def enqueue(
        self,
        task_id: str,
        agent_id: str,
        prompt: str,
        priority: int = 1,
        provider: str = "claude",
    ) -> None:
        """Add a task to the priority queue."""
        item = PriorityItem.create(task_id, agent_id, prompt, priority, provider)
        heappush(self._heap, item)
        self._size += 1
        self._event.set()
        logger.debug("enqueued task %s (priority %d, queue size %d)", task_id[:8], priority, self._size)

    async def dequeue(self, timeout: float | None = None) -> PriorityItem | None:
        """Get the highest priority task. Blocks until available or timeout."""
        if not self._heap:
            self._event.clear()
            try:
                await asyncio.wait_for(self._event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return None

        if self._heap:
            item = heappop(self._heap)
            self._size -= 1
            if not self._heap:
                self._event.clear()
            return item
        return None

    def peek(self) -> PriorityItem | None:
        """Look at the next task without removing it."""
        return self._heap[0] if self._heap else None

    @property
    def size(self) -> int:
        return self._size

    @property
    def is_empty(self) -> bool:
        return self._size == 0

    def clear(self) -> int:
        """Remove all tasks. Returns count removed."""
        count = self._size
        self._heap.clear()
        self._size = 0
        self._event.clear()
        return count

    def boost_aged(self, max_wait_seconds: float = 300) -> int:
        """Boost priority of tasks that have waited too long.

        Tasks waiting longer than max_wait_seconds get boosted to urgent.
        Returns count of boosted tasks.
        """
        now = time.time()
        boosted = 0
        new_heap = []

        for item in self._heap:
            age = now - item.enqueued_at
            if age > max_wait_seconds and item.sort_key[0] > -3:
                # Boost to urgent (-3)
                item = PriorityItem(
                    sort_key=(-3, item.sort_key[1]),
                    task_id=item.task_id,
                    agent_id=item.agent_id,
                    prompt=item.prompt,
                    provider=item.provider,
                    enqueued_at=item.enqueued_at,
                )
                boosted += 1
            heappush(new_heap, item)

        self._heap = new_heap
        if boosted:
            logger.info("boosted %d aged tasks to urgent", boosted)
        return boosted
