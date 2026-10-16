"""Tests for engine.notifications — user notification system."""

import pytest
from engine.notifications import NotificationService, Notification, NotificationLevel
from engine.events import EventBus


@pytest.fixture
def components():
    bus = EventBus()
    svc = NotificationService(bus)
    return svc, bus


class TestNotificationService:
    @pytest.mark.asyncio
    async def test_send_notification(self, components):
        svc, bus = components
        received = []
        bus.on("notification", lambda t, p: received.append(p))

        await svc.send(Notification(
            title="Test", message="Hello", level=NotificationLevel.INFO,
        ))
        assert len(received) == 1
        assert received[0]["title"] == "Test"
        assert received[0]["level"] == "info"

    @pytest.mark.asyncio
    async def test_auto_notification_on_complete(self, components):
        svc, bus = components
        received = []
        bus.on("notification", lambda t, p: received.append(p))

        await bus.emit("task:completed", {"task_id": "abc123"})
        assert len(received) == 1
        assert received[0]["level"] == "success"
        assert "abc123" in received[0]["message"]

    @pytest.mark.asyncio
    async def test_auto_notification_on_failure(self, components):
        svc, bus = components
        received = []
        bus.on("notification", lambda t, p: received.append(p))

        await bus.emit("task:failed", {"task_id": "def456", "error": "timeout"})
        assert len(received) == 1
        assert received[0]["level"] == "error"
        assert "timeout" in received[0]["message"]

    @pytest.mark.asyncio
    async def test_ws_receives_notification(self, components):
        svc, bus = components
        q = bus.subscribe_ws()

        await svc.send(Notification(
            title="WebSocket Test", message="Should arrive via WS",
        ))
        event = q.get_nowait()
        assert event["type"] == "notification"
        assert event["payload"]["title"] == "WebSocket Test"

    @pytest.mark.asyncio
    async def test_notification_with_task_context(self, components):
        svc, bus = components
        received = []
        bus.on("notification", lambda t, p: received.append(p))

        await svc.send(Notification(
            title="Done",
            message="Review complete",
            level=NotificationLevel.SUCCESS,
            task_id="task-123",
            agent_id="claude-code",
        ))
        assert received[0]["task_id"] == "task-123"
        assert received[0]["agent_id"] == "claude-code"
