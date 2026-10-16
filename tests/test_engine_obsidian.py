"""Tests for engine.obsidian_export — task history export to Obsidian."""

import pytest
from datetime import datetime, timezone

from engine.obsidian_export import ObsidianTaskExporter
from engine.store import StoreManager
from engine.models import Task, TaskStatus, TaskMessage


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path / "db")
    store.connect()
    vault = tmp_path / "obsidian-vault"
    vault.mkdir()
    exporter = ObsidianTaskExporter(store, vault_path=vault)
    yield exporter, store, vault
    store.close()


class TestObsidianExport:
    def test_empty_export(self, components):
        exporter, store, vault = components
        count = exporter.export_recent(days=1)
        assert count == 0

    def test_export_with_tasks(self, components):
        exporter, store, vault = components

        # Create a completed task
        t = Task(
            agent_id="claude-code",
            title="Fix auth bug",
            description="Fix the JWT validation",
            status=TaskStatus.COMPLETED,
        )
        store.insert_task(t)
        store.update_task_status(
            t.id, TaskStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        # Add some messages
        store.insert_task_message(TaskMessage(
            task_id=t.id, seq=1,
            author_type="agent", author_id="claude-code",
            type="text", content="Analyzing auth module...",
        ))
        store.insert_task_message(TaskMessage(
            task_id=t.id, seq=2,
            author_type="agent", author_id="claude-code",
            type="tool_use", tool="read",
        ))

        count = exporter.export_recent(days=1)
        assert count == 1

        # Check file exists
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = vault / "06-osborn" / "agent-logs" / f"{today}-agent-log.md"
        assert log_file.exists()

        content = log_file.read_text()
        assert "Fix auth bug" in content
        assert "claude-code" in content
        assert "Analyzing auth module" in content
        assert "vox-agents" in content

    def test_export_groups_by_agent(self, components):
        exporter, store, vault = components

        store.insert_task(Task(agent_id="claude", title="task A"))
        store.insert_task(Task(agent_id="codex", title="task B"))

        count = exporter.export_recent(days=1)
        assert count == 1

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = vault / "06-osborn" / "agent-logs" / f"{today}-agent-log.md"
        content = log_file.read_text()
        assert "## claude" in content
        assert "## codex" in content

    def test_index_file_created(self, components):
        exporter, store, vault = components
        store.insert_task(Task(agent_id="claude", title="test"))
        exporter.export_recent(days=1)

        index = vault / "06-osborn" / "agent-logs" / "VoxAgents-Index.md"
        assert index.exists()
        content = index.read_text()
        assert "Agent" in content or "agent-log" in content

    def test_no_vault_returns_zero(self, tmp_path):
        store = StoreManager(tmp_path / "db")
        store.connect()
        exporter = ObsidianTaskExporter(store, vault_path=tmp_path / "nonexistent")
        count = exporter.export_recent(days=1)
        assert count == 0
        store.close()

    def test_status_emojis(self, components):
        exporter, store, vault = components

        for status, title in [
            (TaskStatus.COMPLETED, "done task"),
            (TaskStatus.FAILED, "failed task"),
            (TaskStatus.CANCELLED, "cancelled task"),
        ]:
            t = Task(agent_id="claude", title=title, status=status)
            store.insert_task(t)
            store.update_task_status(t.id, status)

        exporter.export_recent(days=1)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        content = (vault / "06-osborn" / "agent-logs" / f"{today}-agent-log.md").read_text()

        assert "✅" in content
        assert "❌" in content
        assert "🗑️" in content

    def test_frontmatter(self, components):
        exporter, store, vault = components
        store.insert_task(Task(agent_id="claude", title="test"))
        exporter.export_recent(days=1)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        content = (vault / "06-osborn" / "agent-logs" / f"{today}-agent-log.md").read_text()
        assert content.startswith("---")
        assert "tags: [vox-agents, agent-log]" in content
