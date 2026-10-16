"""Tests for engine.backup — automated data backup."""

import pytest
import sqlite3
from engine.backup import BackupManager


@pytest.fixture
def mgr(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Create fake data files
    with sqlite3.connect(data_dir / "tasks.db") as connection:
        connection.execute("CREATE TABLE task (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO task VALUES ('kept')")
    (data_dir / "agents.json").write_text('[]')

    return BackupManager(data_dir=data_dir, backup_dir=tmp_path / "backups")


class TestBackup:
    def test_defaults_use_vox_agent_namespace(self, monkeypatch, tmp_path):
        canonical = tmp_path / "Vox Agent"
        monkeypatch.setenv("VOXAGENT_DATA_DIR", str(canonical))

        default_manager = BackupManager()

        assert default_manager.data_dir == canonical
        assert default_manager.backup_dir == canonical / "backups"

    def test_create(self, mgr):
        result = mgr.create("test")
        assert result["files"] >= 2
        assert "test" in result["name"]

    def test_create_includes_committed_wal_pages(self, mgr):
        database = mgr.data_dir / "tasks.db"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO task VALUES ('wal-row')")
            connection.commit()

            manifest = mgr.create("wal")

        backup = mgr.backup_dir / manifest["name"] / "tasks.db"
        with sqlite3.connect(backup) as connection:
            ids = {row[0] for row in connection.execute("SELECT id FROM task")}
        assert ids == {"kept", "wal-row"}

    def test_list(self, mgr):
        mgr.create("first")
        mgr.create("second")
        backups = mgr.list_backups()
        assert len(backups) == 2

    def test_restore(self, mgr, tmp_path):
        manifest = mgr.create("restore-test")
        # Delete original
        (tmp_path / "data" / "tasks.db").unlink()
        assert not (tmp_path / "data" / "tasks.db").exists()
        # Restore
        assert mgr.restore(manifest["name"])
        assert (tmp_path / "data" / "tasks.db").exists()

    def test_delete(self, mgr):
        manifest = mgr.create("delete-me")
        assert mgr.delete(manifest["name"])
        assert len(mgr.list_backups()) == 0

    def test_cleanup(self, mgr):
        for i in range(8):
            mgr.create(f"b{i}")
        removed = mgr.cleanup(keep_latest=3)
        assert removed == 5
        assert len(mgr.list_backups()) == 3

    def test_restore_nonexistent(self, mgr):
        assert not mgr.restore("nope")

    def test_empty_list(self, tmp_path):
        mgr = BackupManager(tmp_path / "data", tmp_path / "empty")
        assert mgr.list_backups() == []
