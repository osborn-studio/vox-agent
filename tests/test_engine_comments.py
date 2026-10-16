"""Tests for engine.comments — task annotations."""

import pytest
from engine.comments import CommentManager
from engine.store import StoreManager
from engine.models import Task


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    comments = CommentManager(store)
    yield comments, store
    store.close()


class TestComments:
    def test_add_comment(self, components):
        cm, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        c = cm.add(t.id, "Great work!")
        assert c.id
        assert c.content == "Great work!"

    def test_list_comments(self, components):
        cm, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        cm.add(t.id, "First")
        cm.add(t.id, "Second")
        comments = cm.list_for_task(t.id)
        assert len(comments) == 2
        assert comments[0].content == "First"

    def test_delete_comment(self, components):
        cm, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        c = cm.add(t.id, "To delete")
        assert cm.delete(c.id)
        assert cm.count_for_task(t.id) == 0

    def test_delete_nonexistent(self, components):
        cm, _ = components
        assert not cm.delete("nope")

    def test_count(self, components):
        cm, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        assert cm.count_for_task(t.id) == 0
        cm.add(t.id, "One")
        cm.add(t.id, "Two")
        assert cm.count_for_task(t.id) == 2

    def test_author_types(self, components):
        cm, store = components
        t = Task(agent_id="claude", title="test")
        store.insert_task(t)
        cm.add(t.id, "User note", "user", "gilbert")
        cm.add(t.id, "Agent note", "agent", "claude")
        cm.add(t.id, "System note", "system", "scheduler")
        comments = cm.list_for_task(t.id)
        types = [c.author_type for c in comments]
        assert types == ["user", "agent", "system"]

    def test_separate_tasks(self, components):
        cm, store = components
        t1 = Task(agent_id="claude", title="task 1")
        t2 = Task(agent_id="claude", title="task 2")
        store.insert_task(t1)
        store.insert_task(t2)
        cm.add(t1.id, "For task 1")
        cm.add(t2.id, "For task 2")
        assert cm.count_for_task(t1.id) == 1
        assert cm.count_for_task(t2.id) == 1
