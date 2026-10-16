"""Tests for engine.health — comprehensive health diagnostics."""

import pytest

from engine.health import HealthChecker
from engine.store import StoreManager
from engine.agent_memory import AgentMemory
from engine.skills import SkillLoader
from engine.tools import ToolRegistry
from engine.tools.file_tools import register_file_tools
from engine.models import Task, TaskStatus


@pytest.fixture
def checker(tmp_path):
    store = StoreManager(tmp_path / "db")
    store.connect()
    memory = AgentMemory(tmp_path / "memory")
    skills = SkillLoader(tmp_path / "skills")
    tools = ToolRegistry()
    register_file_tools(tools)

    hc = HealthChecker(store, memory, skills, tools)
    yield hc, store
    store.close()


class TestHealthChecker:
    def test_healthy_empty(self, checker):
        hc, _ = checker
        report = hc.check()
        assert report.status == "healthy"
        assert report.uptime_seconds > 0
        assert report.tools == 5  # file tools

    def test_databases_exist(self, checker):
        hc, _ = checker
        report = hc.check()
        assert "tasks.db" in report.databases
        assert report.databases["tasks.db"]["exists"]

    def test_task_counts(self, checker):
        hc, store = checker
        store.insert_task(Task(agent_id="claude", title="a", status=TaskStatus.COMPLETED))
        store.insert_task(Task(agent_id="claude", title="b", status=TaskStatus.FAILED))
        store.insert_task(Task(agent_id="claude", title="c"))

        report = hc.check()
        assert report.tasks["total"] == 3
        assert report.tasks["completed"] == 1
        assert report.tasks["failed"] == 1

    def test_high_error_rate_warning(self, checker):
        hc, store = checker
        # Create mostly failed tasks
        for i in range(10):
            t = Task(agent_id="claude", title=f"task-{i}")
            store.insert_task(t)
            if i < 7:
                store.update_task_status(t.id, TaskStatus.FAILED)
            else:
                store.update_task_status(t.id, TaskStatus.COMPLETED)

        report = hc.check()
        assert report.status == "degraded"
        assert report.recent_error_rate > 0.5
        assert any("error rate" in w for w in report.warnings)

    def test_memory_count(self, checker):
        hc, _ = checker
        hc.memory.add("agent-1", "test")
        hc.memory.add("agent-2", "test")
        report = hc.check()
        assert report.memory_agents == 2

    def test_zero_error_rate(self, checker):
        hc, store = checker
        for i in range(5):
            t = Task(agent_id="claude", title=f"ok-{i}")
            store.insert_task(t)
            store.update_task_status(t.id, TaskStatus.COMPLETED)

        report = hc.check()
        assert report.recent_error_rate == 0.0
