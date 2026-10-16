"""Tests for engine.priority_queue — priority-ordered task dispatch."""

import asyncio
import time
import pytest
from engine.priority_queue import PriorityTaskQueue, PriorityItem


@pytest.fixture
def queue():
    return PriorityTaskQueue()


class TestPriorityQueue:
    @pytest.mark.asyncio
    async def test_enqueue_dequeue(self, queue):
        queue.enqueue("t1", "claude", "do something", priority=1)
        item = await queue.dequeue(timeout=1)
        assert item is not None
        assert item.task_id == "t1"

    @pytest.mark.asyncio
    async def test_priority_order(self, queue):
        queue.enqueue("low", "claude", "low task", priority=0)
        queue.enqueue("urgent", "claude", "urgent task", priority=3)
        queue.enqueue("medium", "claude", "medium task", priority=1)

        items = []
        for _ in range(3):
            item = await queue.dequeue(timeout=1)
            items.append(item.task_id)

        assert items == ["urgent", "medium", "low"]

    @pytest.mark.asyncio
    async def test_fifo_within_priority(self, queue):
        queue.enqueue("first", "claude", "a", priority=1)
        await asyncio.sleep(0.01)
        queue.enqueue("second", "claude", "b", priority=1)

        i1 = await queue.dequeue(timeout=1)
        i2 = await queue.dequeue(timeout=1)
        assert i1.task_id == "first"
        assert i2.task_id == "second"

    @pytest.mark.asyncio
    async def test_empty_timeout(self, queue):
        item = await queue.dequeue(timeout=0.1)
        assert item is None

    @pytest.mark.asyncio
    async def test_size(self, queue):
        assert queue.size == 0
        assert queue.is_empty
        queue.enqueue("t1", "claude", "x")
        assert queue.size == 1
        assert not queue.is_empty
        await queue.dequeue(timeout=1)
        assert queue.size == 0

    def test_peek(self, queue):
        assert queue.peek() is None
        queue.enqueue("t1", "claude", "x", priority=2)
        queue.enqueue("t2", "claude", "y", priority=1)
        assert queue.peek().task_id == "t1"  # higher priority

    def test_clear(self, queue):
        for i in range(5):
            queue.enqueue(f"t{i}", "claude", "x")
        count = queue.clear()
        assert count == 5
        assert queue.is_empty

    def test_boost_aged(self, queue):
        # Add items with fake old timestamps
        item = PriorityItem.create("old", "claude", "old task", priority=0)
        item = PriorityItem(
            sort_key=item.sort_key,
            task_id="old", agent_id="claude", prompt="old task",
            provider="claude",
            enqueued_at=time.time() - 600,  # 10 minutes ago
        )
        queue._heap.append(item)
        queue._size = 1

        queue.enqueue("new", "claude", "new task", priority=0)

        boosted = queue.boost_aged(max_wait_seconds=300)
        assert boosted == 1

    @pytest.mark.asyncio
    async def test_concurrent_enqueue_dequeue(self, queue):
        """Multiple producers and consumers."""
        results = []

        async def producer(start, count):
            for i in range(count):
                queue.enqueue(f"t{start+i}", "claude", "task", priority=i % 3)
                await asyncio.sleep(0.01)

        async def consumer(count):
            for _ in range(count):
                item = await queue.dequeue(timeout=2)
                if item:
                    results.append(item.task_id)

        await asyncio.gather(
            producer(0, 5),
            producer(10, 5),
            consumer(10),
        )
        assert len(results) == 10
