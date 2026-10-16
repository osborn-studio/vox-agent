"""Tests for engine.concurrency — per-agent and global task limits."""

import asyncio
import pytest
from engine.concurrency import ConcurrencyController


class TestConcurrencyController:
    @pytest.mark.asyncio
    async def test_basic_acquire_release(self):
        ctrl = ConcurrencyController(global_max=5)
        async with ctrl.acquire("agent-1"):
            assert ctrl.active_count == 1
            assert ctrl.agent_active_count("agent-1") == 1
        assert ctrl.active_count == 0

    @pytest.mark.asyncio
    async def test_multiple_agents_parallel(self):
        ctrl = ConcurrencyController(global_max=5)

        async def run_task(agent_id, duration):
            async with ctrl.acquire(agent_id):
                await asyncio.sleep(duration)

        # Run 3 agents in parallel
        await asyncio.gather(
            run_task("a", 0.1),
            run_task("b", 0.1),
            run_task("c", 0.1),
        )
        assert ctrl.active_count == 0

    @pytest.mark.asyncio
    async def test_per_agent_limit(self):
        ctrl = ConcurrencyController(global_max=10)
        ctrl.set_agent_limit("claude", 1)

        acquired = []

        async def try_acquire(n):
            async with ctrl.acquire("claude", max_per_agent=1):
                acquired.append(n)
                await asyncio.sleep(0.1)

        # Two tasks for same agent with limit 1 — should serialize
        start = asyncio.get_event_loop().time()
        await asyncio.gather(try_acquire(1), try_acquire(2))
        elapsed = asyncio.get_event_loop().time() - start

        # Should take ~0.2s (serialized), not ~0.1s (parallel)
        assert elapsed >= 0.18
        assert len(acquired) == 2

    @pytest.mark.asyncio
    async def test_global_limit(self):
        ctrl = ConcurrencyController(global_max=2)

        running = []

        async def run_task(n):
            async with ctrl.acquire(f"agent-{n}"):
                running.append(n)
                # At most 2 should be running simultaneously
                assert ctrl.active_count <= 2
                await asyncio.sleep(0.1)
                running.remove(n)

        await asyncio.gather(*[run_task(i) for i in range(5)])
        assert ctrl.active_count == 0

    @pytest.mark.asyncio
    async def test_status(self):
        ctrl = ConcurrencyController(global_max=5)
        ctrl.set_agent_limit("claude", 2)

        async with ctrl.acquire("claude", max_per_agent=2):
            status = ctrl.status()
            assert status["global_max"] == 5
            assert status["active_total"] == 1
            assert "claude" in status["per_agent"]

    @pytest.mark.asyncio
    async def test_exception_releases(self):
        ctrl = ConcurrencyController(global_max=5)

        with pytest.raises(ValueError):
            async with ctrl.acquire("agent"):
                raise ValueError("boom")

        # Should be released despite exception
        assert ctrl.active_count == 0

    @pytest.mark.asyncio
    async def test_nested_agents(self):
        ctrl = ConcurrencyController(global_max=5)

        async with ctrl.acquire("parent"):
            assert ctrl.active_count == 1
            async with ctrl.acquire("child"):
                assert ctrl.active_count == 2
            assert ctrl.active_count == 1
        assert ctrl.active_count == 0
