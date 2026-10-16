"""Tests for engine.tags — task categorization."""

import pytest
from engine.tags import TagManager
from engine.store import StoreManager
from engine.models import Task


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    tags = TagManager(store)
    yield tags, store
    store.close()


class TestTagManager:
    def test_add_tag(self, components):
        tags, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        tags.add_tag(t.id, "bug")
        assert "bug" in tags.get_tags(t.id)

    def test_multiple_tags(self, components):
        tags, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        tags.add_tag(t.id, "bug")
        tags.add_tag(t.id, "urgent")
        assert tags.get_tags(t.id) == ["bug", "urgent"]

    def test_remove_tag(self, components):
        tags, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        tags.add_tag(t.id, "bug")
        tags.add_tag(t.id, "feature")
        tags.remove_tag(t.id, "bug")
        assert tags.get_tags(t.id) == ["feature"]

    def test_no_duplicate_tags(self, components):
        tags, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        tags.add_tag(t.id, "bug")
        tags.add_tag(t.id, "bug")
        assert tags.get_tags(t.id) == ["bug"]

    def test_empty_tags(self, components):
        tags, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        assert tags.get_tags(t.id) == []

    def test_find_by_tag(self, components):
        tags, store = components
        t1 = Task(agent_id="claude", title="bug fix")
        t2 = Task(agent_id="claude", title="feature")
        store.insert_task(t1)
        store.insert_task(t2)
        tags.add_tag(t1.id, "bug")
        tags.add_tag(t2.id, "feature")
        results = tags.find_by_tag("bug")
        assert len(results) == 1

    def test_all_tags(self, components):
        tags, store = components
        for i in range(3):
            t = Task(agent_id="claude", title=f"task-{i}")
            store.insert_task(t)
            tags.add_tag(t.id, "common")
        t = Task(agent_id="claude", title="special")
        store.insert_task(t)
        tags.add_tag(t.id, "rare")

        all_t = tags.all_tags()
        assert all_t["common"] == 3
        assert all_t["rare"] == 1


class TestAutoTag:
    def test_auto_bug(self, components):
        tags, store = components
        t = Task(agent_id="claude", title="fix login bug")
        store.insert_task(t)
        detected = tags.auto_tag(t.id, "fix the login bug in auth.py")
        assert "bug" in detected

    def test_auto_test(self, components):
        tags, store = components
        t = Task(agent_id="claude", title="add tests")
        store.insert_task(t)
        detected = tags.auto_tag(t.id, "write pytest tests for the engine")
        assert "test" in detected

    def test_auto_multiple(self, components):
        tags, store = components
        t = Task(agent_id="claude", title="security fix")
        store.insert_task(t)
        detected = tags.auto_tag(t.id, "fix security vulnerability in auth")
        assert "bug" in detected
        assert "security" in detected

    def test_no_auto_tags(self, components):
        tags, store = components
        t = Task(agent_id="claude", title="random")
        store.insert_task(t)
        detected = tags.auto_tag(t.id, "今天天气不错")
        assert detected == []
