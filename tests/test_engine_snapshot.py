"""Tests for engine.snapshot — engine state snapshots."""

import pytest

from engine.snapshot import SnapshotManager
from engine.store import StoreManager
from engine.agent_memory import AgentMemory
from engine.skills import SkillLoader
from engine.agent_config import AgentConfigScanner
from engine.models import Task


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path / "db")
    store.connect()
    memory = AgentMemory(tmp_path / "memory")
    skills = SkillLoader(tmp_path / "skills")
    (tmp_path / "skills").mkdir()
    scanner = AgentConfigScanner(tmp_path / "agents")
    (tmp_path / "agents").mkdir()

    mgr = SnapshotManager(store, memory, skills, scanner, tmp_path / "snapshots")
    yield mgr, store, memory
    store.close()


class TestSnapshot:
    def test_create(self, components):
        mgr, store, _ = components
        store.insert_task(Task(agent_id="claude", title="test"))
        snap = mgr.create("test")
        assert snap.id.endswith("-test")
        assert len(snap.tasks) == 1
        assert snap.metadata["task_count"] == 1

    def test_list(self, components):
        mgr, _, _ = components
        mgr.create("first")
        mgr.create("second")
        snapshots = mgr.list_snapshots()
        assert len(snapshots) == 2

    def test_load(self, components):
        mgr, store, _ = components
        store.insert_task(Task(agent_id="claude", title="persistent"))
        snap = mgr.create("load-test")
        loaded = mgr.load(snap.id)
        assert loaded is not None
        assert len(loaded.tasks) == 1

    def test_load_nonexistent(self, components):
        mgr, _, _ = components
        assert mgr.load("nope") is None

    def test_delete(self, components):
        mgr, _, _ = components
        snap = mgr.create("delete-me")
        assert mgr.delete(snap.id)
        assert mgr.load(snap.id) is None

    def test_with_memory(self, components):
        mgr, _, memory = components
        memory.add("claude", "important fact")
        snap = mgr.create()
        assert "claude" in snap.memory
        assert len(snap.memory["claude"]) == 1

    def test_cleanup(self, components):
        mgr, _, _ = components
        for i in range(15):
            mgr.create(f"snap-{i}")
        removed = mgr.cleanup(keep_latest=5)
        assert removed == 10
        assert len(mgr.list_snapshots()) == 5
