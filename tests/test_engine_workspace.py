"""Tests for engine.workspace — isolated task working directories."""

import pytest
import time
from engine.workspace import WorkspaceManager


@pytest.fixture
def mgr(tmp_path):
    return WorkspaceManager(tmp_path / "workspaces")


class TestWorkspaceManager:
    def test_create_workspace(self, mgr):
        ws = mgr.create("task-123", "claude")
        assert ws.created
        assert ws.path.exists()
        assert ws.path.is_dir()

    def test_idempotent_create(self, mgr):
        ws1 = mgr.create("task-123", "claude")
        ws2 = mgr.create("task-123", "claude")
        assert ws1.path == ws2.path
        assert not ws2.created

    def test_get_path(self, mgr):
        mgr.create("task-1", "claude")
        assert mgr.get_path("task-1", "claude") is not None
        assert mgr.get_path("nope", "claude") is None

    def test_cleanup(self, mgr):
        mgr.create("task-1", "claude")
        assert mgr.cleanup("task-1", "claude")
        assert mgr.get_path("task-1", "claude") is None

    def test_cleanup_nonexistent(self, mgr):
        assert not mgr.cleanup("nope", "claude")

    def test_separate_agents(self, mgr):
        ws1 = mgr.create("task-1", "claude")
        ws2 = mgr.create("task-1", "codex")
        assert ws1.path != ws2.path

    def test_list_active(self, mgr):
        mgr.create("task-1", "claude")
        mgr.create("task-2", "codex")
        active = mgr.list_active()
        assert len(active) == 2

    def test_list_with_files(self, mgr):
        ws = mgr.create("task-1", "claude")
        (ws.path / "output.txt").write_text("hello")
        active = mgr.list_active()
        assert active[0]["file_count"] == 1
        assert active[0]["size_bytes"] > 0

    def test_disk_usage(self, mgr):
        ws = mgr.create("task-1", "claude")
        (ws.path / "big.txt").write_text("x" * 10000)
        usage = mgr.disk_usage()
        assert usage["total_bytes"] >= 10000
        assert usage["workspace_count"] == 1

    def test_cleanup_all_old(self, mgr):
        ws = mgr.create("old-task", "claude")
        import os
        old_time = time.time() - (25 * 3600)
        os.utime(ws.path, (old_time, old_time))

        mgr.create("new-task", "claude")
        removed = mgr.cleanup_all(max_age_hours=24)
        assert removed == 1
        assert len(mgr.list_active()) == 1
