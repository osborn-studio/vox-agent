"""Tests for the copy-only V2 to Vox Agent data migration."""

import json
import sqlite3
import pytest

from engine.migrate import V2Migrator


@pytest.fixture
def v2_setup(tmp_path):
    """Create mock V2 data."""
    v2_dir = tmp_path / ".voxlog2"
    v2_dir.mkdir()
    vox_dir = tmp_path / ".voxagent"

    # Create V2 history database
    db_path = v2_dir / "history.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE voice_log (
            id INTEGER PRIMARY KEY,
            created_at TEXT,
            raw_text TEXT,
            polished_text TEXT,
            duration_seconds REAL,
            stt_provider TEXT,
            agent TEXT
        )
    """)
    for i in range(5):
        conn.execute(
            "INSERT INTO voice_log (created_at, raw_text, polished_text, duration_seconds, stt_provider, agent) VALUES (?, ?, ?, ?, ?, ?)",
            (f"2026-04-0{i+1}T10:00:00Z", f"raw {i}", f"polished {i}", 3.5, "qwen", "claude"),
        )
    conn.commit()
    conn.close()

    # Create V2 terms.json
    terms = {
        "corrections": {
            "os born": "OSBORN",
            "claw code": "Claude Code",
        },
        "preserve": ["OSBORN", "Claude Code", "FastAPI"],
    }
    (tmp_path / "voxlog").mkdir()
    (tmp_path / "voxlog" / "terms.json").write_text(json.dumps(terms))

    migrator = V2Migrator(v2_data_dir=v2_dir, vox_data_dir=vox_dir)
    return migrator, v2_dir, vox_dir


class TestMigration:
    def test_needs_migration(self, v2_setup):
        migrator, _, _ = v2_setup
        assert migrator.needs_migration()

    def test_no_migration_when_already_done(self, v2_setup):
        migrator, _, vox_dir = v2_setup
        vox_dir.mkdir(parents=True, exist_ok=True)
        (vox_dir / ".legacy-voxlog-migrated").write_text("done")
        assert not migrator.needs_migration()

    def test_no_migration_without_v2(self, tmp_path):
        migrator = V2Migrator(
            v2_data_dir=tmp_path / "nonexistent",
            vox_data_dir=tmp_path / "vox",
        )
        assert not migrator.needs_migration()

    def test_migrate_history(self, v2_setup):
        migrator, _, vox_dir = v2_setup
        result = migrator.migrate()

        assert result.success
        assert result.records_migrated == 5

        # Verify canonical Vox Agent target database.
        target_db = vox_dir / "history.db"
        assert target_db.exists()

        conn = sqlite3.connect(str(target_db))
        count = conn.execute("SELECT COUNT(*) FROM voice_events").fetchone()[0]
        assert count == 5

        row = conn.execute("SELECT * FROM voice_events LIMIT 1").fetchone()
        conn.close()
        assert row is not None

    def test_migrate_terms(self, v2_setup, tmp_path):
        migrator, _, vox_dir = v2_setup

        terms = {"corrections": {"test": "Test"}, "preserve": ["OSBORN"]}
        v2_terms = migrator.v2_dir / "terms.json"
        v2_terms.write_text(json.dumps(terms))

        result = migrator.migrate()
        migrated = json.loads((vox_dir / "terms.json").read_text())

        assert result.terms_migrated == 2
        assert migrated["corrections"]["test"] == "Test"
        assert "OSBORN" in migrated["preserve"]
        assert not migrator.needs_migration()  # marker created

    def test_migration_marker(self, v2_setup):
        migrator, _, vox_dir = v2_setup
        migrator.migrate()

        marker = vox_dir / ".legacy-voxlog-migrated"
        assert marker.exists()
        assert "records_migrated" in marker.read_text()

    def test_idempotent(self, v2_setup):
        migrator, _, _ = v2_setup
        r1 = migrator.migrate()
        r2 = migrator.migrate()
        # Second run should be a no-op (marker exists)
        assert r2.records_migrated == 0

    def test_source_database_is_not_modified(self, v2_setup):
        migrator, v2_dir, _ = v2_setup
        source = v2_dir / "history.db"
        before = source.read_bytes()
        before_mtime = source.stat().st_mtime_ns

        migrator.migrate()

        assert source.read_bytes() == before
        assert source.stat().st_mtime_ns == before_mtime
