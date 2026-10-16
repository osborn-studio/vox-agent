"""Tests for engine.events — EventBus dispatch and WebSocket subscription."""

import pytest
from engine.events import EventBus


@pytest.fixture
def bus():
    return EventBus()


class TestEventBus:
    @pytest.mark.asyncio
    async def test_emit_to_typed_listener(self, bus):
        received = []
        async def handler(event_type, payload):
            received.append((event_type, payload))

        bus.on("task:created", handler)
        await bus.emit("task:created", {"id": "t1"})
        assert len(received) == 1
        assert received[0] == ("task:created", {"id": "t1"})

    @pytest.mark.asyncio
    async def test_wildcard_listener(self, bus):
        received = []
        async def handler(event_type, payload):
            received.append(event_type)

        bus.on_all(handler)
        await bus.emit("task:created", {})
        await bus.emit("task:completed", {})
        assert received == ["task:created", "task:completed"]

    @pytest.mark.asyncio
    async def test_ws_subscription(self, bus):
        q = bus.subscribe_ws()
        await bus.emit("task:message", {"text": "hello"})
        msg = q.get_nowait()
        assert msg["type"] == "task:message"
        assert msg["payload"] == {"text": "hello"}

    @pytest.mark.asyncio
    async def test_ws_unsubscribe(self, bus):
        q = bus.subscribe_ws()
        bus.unsubscribe_ws(q)
        await bus.emit("task:message", {"text": "dropped"})
        assert q.empty()

    @pytest.mark.asyncio
    async def test_multiple_ws_clients(self, bus):
        q1 = bus.subscribe_ws()
        q2 = bus.subscribe_ws()
        await bus.emit("test", {"n": 1})
        assert q1.get_nowait()["payload"] == {"n": 1}
        assert q2.get_nowait()["payload"] == {"n": 1}

    @pytest.mark.asyncio
    async def test_handler_error_does_not_crash(self, bus):
        async def bad_handler(event_type, payload):
            raise ValueError("boom")

        async def good_handler(event_type, payload):
            pass  # should still be called

        bus.on("test", bad_handler)
        bus.on("test", good_handler)
        await bus.emit("test", {})  # should not raise

    @pytest.mark.asyncio
    async def test_no_listeners_is_ok(self, bus):
        await bus.emit("unknown:event", {})  # should not raise
