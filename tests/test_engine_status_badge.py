"""Tests for engine.status_badge — status badge generation."""

import pytest
from engine.status_badge import StatusBadge
from engine.analytics import TaskAnalytics
from engine.health import HealthChecker
from engine.store import StoreManager
from engine.agent_memory import AgentMemory
from engine.skills import SkillLoader
from engine.tools import ToolRegistry
from engine.models import Task, TaskStatus


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path / "db")
    store.connect()
    memory = AgentMemory(tmp_path / "memory")
    skills = SkillLoader(tmp_path / "skills")
    (tmp_path / "skills").mkdir()
    tools = ToolRegistry()
    analytics = TaskAnalytics(store)
    health = HealthChecker(store, memory, skills, tools)
    badge = StatusBadge(analytics, health)
    yield badge, store
    store.close()


class TestStatusBadge:
    def test_engine_badge(self, components):
        badge, _ = components
        b = badge.engine_badge()
        assert b["label"] == "engine"
        assert b["value"] == "healthy"
        assert b["color"] == "brightgreen"

    def test_tasks_badge_empty(self, components):
        badge, _ = components
        b = badge.tasks_badge()
        assert "0 active" in b["value"]

    def test_tasks_badge_active(self, components):
        badge, store = components
        store.insert_task(Task(agent_id="claude", title="a", status=TaskStatus.RUNNING))
        b = badge.tasks_badge()
        assert "1 active" in b["value"]
        assert b["color"] == "blue"

    def test_success_badge(self, components):
        badge, store = components
        for i in range(8):
            t = Task(agent_id="claude", title=f"ok-{i}")
            store.insert_task(t)
            store.update_task_status(t.id, TaskStatus.COMPLETED)
        for i in range(2):
            t = Task(agent_id="claude", title=f"bad-{i}")
            store.insert_task(t)
            store.update_task_status(t.id, TaskStatus.FAILED)
        b = badge.success_badge()
        assert "80%" in b["value"]

    def test_all_badges(self, components):
        badge, _ = components
        badges = badge.all_badges()
        assert len(badges) == 3

    def test_text_summary(self, components):
        badge, _ = components
        text = badge.text_summary()
        assert "Engine:" in text
        assert "Tasks:" in text

    def test_svg(self, components):
        badge, _ = components
        svg = badge.svg(badge.engine_badge())
        assert "<svg" in svg
        assert "engine" in svg
