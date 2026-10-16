"""Tests for engine.heartbeat — agent availability tracking."""

import time
import pytest
from engine.heartbeat import HeartbeatMonitor
from engine.events import EventBus


@pytest.fixture
def components():
    bus = EventBus()
    monitor = HeartbeatMonitor(bus, timeout_seconds=1.0)
    return monitor, bus


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_report_new_agent(self, components):
        monitor, bus = components
        events = []
        bus.on("agent:status", lambda t, p: events.append(p))

        await monitor.report("claude", status="online")
        assert monitor.is_online("claude")
        assert len(events) == 1
        assert events[0]["status"] == "online"

    @pytest.mark.asyncio
    async def test_no_event_on_same_status(self, components):
        monitor, bus = components
        events = []
        bus.on("agent:status", lambda t, p: events.append(p))

        await monitor.report("claude", status="online")
        await monitor.report("claude", status="online")
        assert len(events) == 1  # only first triggers event

    @pytest.mark.asyncio
    async def test_status_change_event(self, components):
        monitor, bus = components
        events = []
        bus.on("agent:status", lambda t, p: events.append(p))

        await monitor.report("claude", status="online")
        await monitor.report("claude", status="working")
        assert len(events) == 2
        assert events[1]["previous"] == "online"
        assert events[1]["status"] == "working"

    @pytest.mark.asyncio
    async def test_stale_detection(self, components):
        monitor, bus = components
        await monitor.report("claude", status="online")

        # Manually set last_seen to past
        monitor._agents["claude"].last_seen = time.time() - 10

        stale = await monitor.check_stale()
        assert "claude" in stale
        assert not monitor.is_online("claude")

    @pytest.mark.asyncio
    async def test_not_stale_within_timeout(self, components):
        monitor, bus = components
        await monitor.report("claude", status="online")
        stale = await monitor.check_stale()
        assert stale == []

    @pytest.mark.asyncio
    async def test_online_agents(self, components):
        monitor, _ = components
        await monitor.report("a", status="online")
        await monitor.report("b", status="working")
        await monitor.report("c", status="offline")

        online = monitor.online_agents()
        assert set(online) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_all_statuses(self, components):
        monitor, _ = components
        await monitor.report("a", status="online", active_tasks=2)
        await monitor.report("b", status="idle")

        statuses = monitor.all_statuses()
        assert len(statuses) == 2

    @pytest.mark.asyncio
    async def test_get_status(self, components):
        monitor, _ = components
        await monitor.report("claude", status="working", active_tasks=3)
        hb = monitor.get_status("claude")
        assert hb.active_tasks == 3
        assert hb.status == "working"

    @pytest.mark.asyncio
    async def test_unknown_agent(self, components):
        monitor, _ = components
        assert monitor.get_status("unknown") is None
        assert not monitor.is_online("unknown")
