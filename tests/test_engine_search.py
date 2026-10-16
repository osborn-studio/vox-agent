"""Tests for engine.search — task history search and filtering."""

import pytest

from engine.search import TaskSearch, SearchQuery
from engine.store import StoreManager
from engine.models import Task, TaskStatus, TaskPriority, TaskMessage


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    search = TaskSearch(store)

    # Seed test data
    tasks = [
        Task(agent_id="claude", title="Fix auth bug", description="JWT validation broken", priority=TaskPriority.HIGH),
        Task(agent_id="claude", title="Write tests", description="Add unit tests for auth", priority=TaskPriority.MEDIUM),
        Task(agent_id="codex", title="Update README", description="Add API docs", priority=TaskPriority.LOW),
        Task(agent_id="claude", title="Deploy staging", description="Deploy to staging env", priority=TaskPriority.URGENT),
        Task(agent_id="codex", title="Fix CSS bug", description="Button alignment issue", priority=TaskPriority.MEDIUM),
    ]
    for t in tasks:
        store.insert_task(t)

    # Complete some
    store.update_task_status(tasks[0].id, TaskStatus.COMPLETED)
    store.update_task_status(tasks[3].id, TaskStatus.FAILED)

    # Add messages to first task
    store.insert_task_message(TaskMessage(
        task_id=tasks[0].id, seq=1,
        author_type="agent", author_id="claude",
        type="text", content="Found the JWT issue in auth.py line 42",
    ))

    yield search, store, tasks
    store.close()


class TestTextSearch:
    def test_search_by_title(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(text="auth"))
        assert result.total == 2  # "Fix auth bug" + "Write tests for auth"

    def test_search_by_description(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(text="JWT"))
        assert result.total == 1

    def test_search_no_match(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(text="nonexistent"))
        assert result.total == 0
        assert result.tasks == []

    def test_search_all(self, components):
        search, _, _ = components
        result = search.search(SearchQuery())
        assert result.total == 5


class TestFilters:
    def test_filter_by_agent(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(agent_id="claude"))
        assert result.total == 3

    def test_filter_by_status(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(status="completed"))
        assert result.total == 1

    def test_filter_by_priority(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(priority=2))  # HIGH and above
        assert all(t["priority"] >= 2 for t in result.tasks)

    def test_combined_filters(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(text="bug", agent_id="claude"))
        assert result.total == 1
        assert result.tasks[0]["title"] == "Fix auth bug"


class TestSorting:
    def test_sort_by_date(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(sort_by="date"))
        dates = [t["created_at"] for t in result.tasks]
        assert dates == sorted(dates, reverse=True)

    def test_sort_by_priority(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(sort_by="priority"))
        priorities = [t["priority"] for t in result.tasks]
        assert priorities == sorted(priorities, reverse=True)

    def test_sort_by_relevance(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(text="auth", sort_by="relevance"))
        # "Fix auth bug" has "auth" in title, should rank first
        assert "auth" in result.tasks[0]["title"].lower()


class TestPagination:
    def test_limit(self, components):
        search, _, _ = components
        result = search.search(SearchQuery(limit=2))
        assert len(result.tasks) == 2
        assert result.total == 5

    def test_offset(self, components):
        search, _, _ = components
        r1 = search.search(SearchQuery(limit=2, offset=0))
        r2 = search.search(SearchQuery(limit=2, offset=2))
        assert r1.tasks[0]["id"] != r2.tasks[0]["id"]


class TestMessageSearch:
    def test_search_messages(self, components):
        search, _, tasks = components
        results = search.search_messages("JWT")
        assert len(results) == 1
        assert "auth.py line 42" in results[0]["content"]

    def test_search_messages_by_task(self, components):
        search, _, tasks = components
        results = search.search_messages("JWT", task_id=tasks[0].id)
        assert len(results) == 1


class TestRecentActivity:
    def test_recent(self, components):
        search, _, _ = components
        results = search.recent_activity(hours=1)
        assert len(results) == 5  # all created just now
