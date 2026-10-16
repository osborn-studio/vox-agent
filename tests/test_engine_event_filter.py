"""Tests for engine.event_filter — selective event subscription."""

import pytest
from engine.event_filter import EventFilter, FILTER_PRESETS


@pytest.fixture
def f():
    return EventFilter()


class TestEventFilter:
    def test_no_filter_delivers_all(self, f):
        assert f.should_deliver("client1", "task:created")
        assert f.should_deliver("client1", "anything")

    def test_subscribe(self, f):
        f.subscribe("c1", ["task:created", "task:completed"])
        assert f.should_deliver("c1", "task:created")
        assert not f.should_deliver("c1", "task:message")

    def test_wildcard(self, f):
        f.subscribe("c1", ["*"])
        assert f.should_deliver("c1", "anything")

    def test_unsubscribe(self, f):
        f.subscribe("c1", ["task:created"])
        f.unsubscribe("c1")
        assert f.should_deliver("c1", "task:message")  # back to all

    def test_add_type(self, f):
        f.subscribe("c1", ["task:created"])
        f.add_type("c1", "task:completed")
        assert f.should_deliver("c1", "task:completed")

    def test_remove_type(self, f):
        f.subscribe("c1", ["task:created", "task:completed"])
        f.remove_type("c1", "task:completed")
        assert not f.should_deliver("c1", "task:completed")

    def test_get_subscription(self, f):
        f.subscribe("c1", ["task:created", "agent:status"])
        sub = f.get_subscription("c1")
        assert "task:created" in sub

    def test_default_subscription(self, f):
        assert f.get_subscription("unknown") == ["*"]

    def test_active_clients(self, f):
        assert f.active_clients() == 0
        f.subscribe("c1", ["task:created"])
        f.subscribe("c2", ["task:completed"])
        assert f.active_clients() == 2

    def test_presets(self):
        assert "tasks_only" in FILTER_PRESETS
        assert "task:created" in FILTER_PRESETS["tasks_only"]
        assert "*" in FILTER_PRESETS["all"]
