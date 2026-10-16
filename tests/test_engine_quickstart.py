"""Tests for engine.quickstart — one-function bootstrap."""

import pytest
from engine.quickstart import create_engine


class TestQuickstart:
    def test_create(self, tmp_path):
        engine = create_engine(data_dir=tmp_path / "data")
        assert engine.store is not None
        assert engine.scheduler is not None
        assert engine.orchestrator is not None
        assert engine.event_bus is not None
        assert len(engine.tool_registry.list_tools()) == 8
        engine.store.close()

    def test_components_accessible(self, tmp_path):
        engine = create_engine(data_dir=tmp_path / "data")
        # Verify all major components exist
        attrs = [
            "store", "event_bus", "scheduler", "agent_engine",
            "orchestrator", "intent_parser", "analytics",
            "health_checker", "cost_tracker", "heartbeat",
            "search", "tag_mgr", "comment_mgr",
            "router", "dashboard", "metrics",
        ]
        for attr in attrs:
            assert hasattr(engine, attr), f"missing: {attr}"
        engine.store.close()

    def test_explicit_data_directory_contains_all_engine_owned_writers(self, tmp_path):
        data_dir = tmp_path / "portable"
        engine = create_engine(data_dir=data_dir)

        assert engine.store.data_dir == data_dir
        assert engine.skill_loader.skills_dir == data_dir / "skills"
        assert engine.agent_memory.memory_dir == data_dir / "memory"
        assert engine.cost_tracker.data_dir == data_dir / "costs"
        assert engine.template_mgr.templates_dir == data_dir / "templates"
        assert engine.profile_mgr.profiles_dir == data_dir / "profiles"
        assert engine.task_log.log_dir == data_dir / "logs"
        assert engine.workspace_mgr.base_dir == data_dir / "workspaces"
        assert engine.plugin_loader.plugins_dir == data_dir / "plugins"
        assert engine.recurring.path == data_dir / "schedules.json"
        assert engine.snapshot_mgr.snapshots_dir == data_dir / "snapshots"
        engine.store.close()

    @pytest.mark.asyncio
    async def test_create_and_use(self, tmp_path):
        engine = create_engine(data_dir=tmp_path / "data")
        result = await engine.orchestrator.process_message(
            "记一下：明天开会", agent_id=None,
        )
        assert result.action == "recorded"
        engine.store.close()
