"""Tests for engine.annotations — structured task metadata."""

import pytest
from engine.annotations import AnnotationManager
from engine.store import StoreManager
from engine.models import Task


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    ann = AnnotationManager(store)
    yield ann, store
    store.close()


class TestAnnotations:
    def test_set_and_get(self, components):
        ann, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        ann.set(t.id, "score", "8/10")
        assert ann.get(t.id, "score") == "8/10"

    def test_upsert(self, components):
        ann, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        ann.set(t.id, "status", "pending")
        ann.set(t.id, "status", "done")
        assert ann.get(t.id, "status") == "done"

    def test_get_all(self, components):
        ann, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        ann.set(t.id, "score", "9")
        ann.set(t.id, "coverage", "87%")
        ann.set(t.id, "target", "staging")
        result = ann.get_all(t.id)
        assert len(result) == 3
        assert result["score"] == "9"

    def test_get_nonexistent(self, components):
        ann, _ = components
        assert ann.get("nope", "key") is None

    def test_delete(self, components):
        ann, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        ann.set(t.id, "key", "value")
        assert ann.delete(t.id, "key")
        assert ann.get(t.id, "key") is None

    def test_delete_all(self, components):
        ann, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        ann.set(t.id, "a", "1")
        ann.set(t.id, "b", "2")
        count = ann.delete_all(t.id)
        assert count == 2
        assert ann.get_all(t.id) == {}

    def test_find_by_annotation(self, components):
        ann, store = components
        t1 = Task(agent_id="claude", title="a")
        t2 = Task(agent_id="claude", title="b")
        store.insert_task(t1)
        store.insert_task(t2)
        ann.set(t1.id, "priority", "high")
        ann.set(t2.id, "priority", "low")
        high = ann.find_by_annotation("priority", "high")
        assert t1.id in high
        assert t2.id not in high

    def test_find_by_key_only(self, components):
        ann, store = components
        t1 = Task(agent_id="claude", title="a")
        t2 = Task(agent_id="claude", title="b")
        store.insert_task(t1)
        store.insert_task(t2)
        ann.set(t1.id, "reviewed", "yes")
        ann.set(t2.id, "reviewed", "no")
        reviewed = ann.find_by_annotation("reviewed")
        assert len(reviewed) == 2
