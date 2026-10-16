"""Tests for engine.task_history — state transition tracking."""

import pytest
from engine.task_history import TaskHistory
from engine.store import StoreManager


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    history = TaskHistory(store)
    yield history, store
    store.close()


class TestTaskHistory:
    def test_record_and_get(self, components):
        h, _ = components
        h.record("t1", "draft", "planned")
        h.record("t1", "planned", "running")
        transitions = h.get_transitions("t1")
        assert len(transitions) == 2
        assert transitions[0].from_status == "draft"
        assert transitions[1].to_status == "running"

    def test_timeline(self, components):
        h, _ = components
        h.record("t1", "draft", "planned")
        h.record("t1", "planned", "running")
        h.record("t1", "running", "completed")
        timeline = h.get_timeline("t1")
        assert len(timeline) == 3
        assert "→" in timeline[0]["change"]

    def test_time_in_status(self, components):
        h, _ = components
        h.record("t1", "draft", "running")
        import time
        time.sleep(0.1)
        h.record("t1", "running", "completed")
        durations = h.time_in_status("t1")
        assert "running" in durations
        assert durations["running"] >= 0.05

    def test_empty(self, components):
        h, _ = components
        assert h.get_transitions("nope") == []
        assert h.get_timeline("nope") == []
        assert h.time_in_status("nope") == {}

    def test_recent(self, components):
        h, _ = components
        for i in range(5):
            h.record(f"t{i}", "draft", "running")
        recent = h.recent_transitions(limit=3)
        assert len(recent) == 3

    def test_actor(self, components):
        h, _ = components
        h.record("t1", "draft", "running", actor="scheduler")
        t = h.get_transitions("t1")[0]
        assert t.actor == "scheduler"

    def test_reason(self, components):
        h, _ = components
        h.record("t1", "running", "failed", reason="timeout")
        t = h.get_transitions("t1")[0]
        assert t.reason == "timeout"

    def test_separate_tasks(self, components):
        h, _ = components
        h.record("t1", "draft", "running")
        h.record("t2", "draft", "planned")
        assert len(h.get_transitions("t1")) == 1
        assert len(h.get_transitions("t2")) == 1
