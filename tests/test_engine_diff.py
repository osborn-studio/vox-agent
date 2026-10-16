"""Tests for engine.diff — task output comparison."""

import pytest
from engine.diff import TaskDiff
from engine.store import StoreManager
from engine.models import Task, TaskStatus, TaskMessage


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    diff = TaskDiff(store)

    # Create two completed tasks with different outputs
    t1 = Task(agent_id="claude", title="review v1")
    t2 = Task(agent_id="claude", title="review v2")
    store.insert_task(t1)
    store.insert_task(t2)
    store.update_task_status(t1.id, TaskStatus.COMPLETED)
    store.update_task_status(t2.id, TaskStatus.COMPLETED)

    store.insert_task_message(TaskMessage(
        task_id=t1.id, seq=1, author_type="agent", author_id="claude",
        type="text", content="Line 1\nLine 2\nLine 3",
    ))
    store.insert_task_message(TaskMessage(
        task_id=t2.id, seq=1, author_type="agent", author_id="claude",
        type="text", content="Line 1\nLine 2 modified\nLine 3\nLine 4",
    ))

    yield diff, store, t1, t2
    store.close()


class TestTaskDiff:
    def test_compare(self, components):
        diff, _, t1, t2 = components
        result = diff.compare_tasks(t1.id, t2.id)
        assert result is not None
        assert result.added_lines > 0
        assert result.similarity > 0.5

    def test_identical(self, components):
        diff, store, t1, _ = components
        result = diff.compare_tasks(t1.id, t1.id)
        assert result is not None
        assert result.added_lines == 0
        assert result.removed_lines == 0
        assert result.similarity == 1.0

    def test_nonexistent(self, components):
        diff, _, _, _ = components
        result = diff.compare_tasks("nope-a", "nope-b")
        assert result is None

    def test_compare_latest(self, components):
        diff, _, _, _ = components
        result = diff.compare_latest_runs("claude")
        assert result is not None

    def test_compare_latest_filtered(self, components):
        diff, _, _, _ = components
        result = diff.compare_latest_runs("claude", title_contains="review")
        assert result is not None

    def test_no_latest(self, components):
        diff, _, _, _ = components
        result = diff.compare_latest_runs("nonexistent")
        assert result is None

    def test_diff_text(self, components):
        diff, _, t1, t2 = components
        result = diff.compare_tasks(t1.id, t2.id)
        assert "modified" in result.diff_text or "+" in result.diff_text
