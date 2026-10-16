"""Tests for engine.dashboard — unified dashboard aggregation."""

import pytest
from engine.dashboard import DashboardAggregator
from engine.store import StoreManager
from engine.analytics import TaskAnalytics
from engine.task_queue_stats import QueueStats
from engine.health import HealthChecker
from engine.cost_tracker import CostTracker
from engine.agent_memory import AgentMemory
from engine.skills import SkillLoader
from engine.tools import ToolRegistry
from engine.tools.file_tools import register_file_tools
from engine.heartbeat import HeartbeatMonitor
from engine.events import EventBus
from engine.models import Task, TaskStatus


@pytest.fixture
def dashboard(tmp_path):
    store = StoreManager(tmp_path / "db")
    store.connect()
    memory = AgentMemory(tmp_path / "memory")
    skills = SkillLoader(tmp_path / "skills")
    (tmp_path / "skills").mkdir()
    tools = ToolRegistry()
    register_file_tools(tools)
    bus = EventBus()
    heartbeat = HeartbeatMonitor(bus)

    analytics = TaskAnalytics(store)
    queue = QueueStats(store)
    health = HealthChecker(store, memory, skills, tools)
    costs = CostTracker(tmp_path / "costs")

    agg = DashboardAggregator(
        store, analytics, queue, health, costs, memory, skills, tools, heartbeat,
    )
    yield agg, store, heartbeat
    store.close()


class TestDashboard:
    def test_empty(self, dashboard):
        agg, _, _ = dashboard
        data = agg.get_dashboard()
        assert data["health"]["status"] == "healthy"
        assert data["tasks"]["total"] == 0
        assert data["infrastructure"]["tools"] >= 5

    def test_with_tasks(self, dashboard):
        agg, store, _ = dashboard
        store.insert_task(Task(agent_id="claude", title="a", status=TaskStatus.COMPLETED))
        store.insert_task(Task(agent_id="claude", title="b", status=TaskStatus.FAILED))
        data = agg.get_dashboard()
        assert data["tasks"]["total"] == 2
        assert data["tasks"]["completed"] == 1
        assert data["tasks"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_with_online_agent(self, dashboard):
        agg, _, heartbeat = dashboard
        await heartbeat.report("claude", "online")
        data = agg.get_dashboard()
        assert data["infrastructure"]["online_agents"] == 1

    def test_costs(self, dashboard):
        agg, _, _ = dashboard
        data = agg.get_dashboard()
        assert "today_usd" in data["costs"]
        assert "over_budget" in data["costs"]

    def test_queue(self, dashboard):
        agg, _, _ = dashboard
        data = agg.get_dashboard()
        assert "depth" in data["queue"]
        assert "throughput" in data["queue"]

    def test_all_sections_present(self, dashboard):
        agg, _, _ = dashboard
        data = agg.get_dashboard()
        assert all(k in data for k in ["health", "tasks", "queue", "agents", "costs", "infrastructure"])
