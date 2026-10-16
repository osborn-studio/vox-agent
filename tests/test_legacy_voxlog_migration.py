from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from engine.legacy_migration import (
    discover_legacy_databases,
    inspect_legacy_database,
    read_legacy_database,
)


def _create_swift_history(path: Path) -> None:
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE recognition_history (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            duration_seconds REAL,
            raw_text TEXT NOT NULL,
            processing_mode TEXT,
            processed_text TEXT,
            final_text TEXT NOT NULL,
            status TEXT NOT NULL,
            character_count INTEGER,
            asr_provider TEXT,
            agent TEXT,
            device_id TEXT,
            sync_updated_at TEXT
        )"""
    )
    connection.executemany(
        """INSERT INTO recognition_history
           (id, created_at, duration_seconds, raw_text, processed_text, final_text,
            status, asr_provider, agent)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                "legacy-1",
                "2026-07-01T10:00:00Z",
                3.5,
                "raw one",
                "processed one",
                "final one",
                "completed",
                "qwen",
                "claude",
            ),
            (
                "legacy-invalid",
                "2026-07-01T10:00:00",
                1.0,
                "missing timezone",
                "",
                "missing timezone",
                "completed",
                "qwen",
                "claude",
            ),
        ],
    )
    connection.commit()
    connection.close()


def test_swift_history_is_read_copy_only_into_vox_agent_contract(tmp_path: Path):
    source = tmp_path / "Library" / "Application Support" / "VoxLog" / "history.db"
    _create_swift_history(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    before_mtime = source.stat().st_mtime_ns

    history = read_legacy_database(source)

    assert history.source.kind == "recognition_history"
    assert history.source.records_found == 2
    assert history.invalid == 1
    assert history.records == [
        {
            "id": "legacy-1",
            "created_at": "2026-07-01T10:00:00+00:00",
            "utterance_id": "",
            "output_id": "",
            "session_id": "",
            "raw_text": "raw one",
            "display_text": "final one",
            "polished_text": "final one",
            "stt_provider": "qwen",
            "stt_model": "",
            "llm_provider": "",
            "latency_ms": 0,
            "target_app": "",
            "target_risk_level": "low",
            "role": "me",
            "recording_mode": "normal",
            "output_mode": "paste",
            "confidence": 0.0,
            "archive_status": "polished",
            "agent": "claude",
        }
    ]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert source.stat().st_mtime_ns == before_mtime


def test_current_voice_events_keep_identity_and_exclude_ephemeral(tmp_path: Path):
    source = tmp_path / ".voxlog2" / "history.db"
    source.parent.mkdir()
    connection = sqlite3.connect(source)
    connection.execute(
        """CREATE TABLE voice_events (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            raw_text TEXT,
            display_text TEXT,
            polished_text TEXT,
            recording_mode TEXT,
            output_mode TEXT,
            archive_status TEXT,
            confidence REAL
        )"""
    )
    connection.executemany(
        "INSERT INTO voice_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "normal-1",
                "2026-07-02T10:00:00+00:00",
                "raw",
                "display",
                "polished",
                "normal",
                "paste",
                "polished",
                0.9,
            ),
            (
                "ephemeral-1",
                "2026-07-02T10:01:00+00:00",
                "private",
                "private",
                "private",
                "ephemeral",
                "none",
                "skipped",
                1.0,
            ),
        ],
    )
    connection.commit()
    connection.close()

    history = read_legacy_database(source)

    assert [record["id"] for record in history.records] == ["normal-1"]
    assert history.invalid == 1


def test_discovery_excludes_canonical_database_and_deduplicates(tmp_path: Path):
    home = tmp_path / "home"
    legacy = home / ".voxlog2" / "history.db"
    canonical = home / ".voxagent" / "history.db"
    legacy.parent.mkdir(parents=True)
    canonical.parent.mkdir(parents=True)
    legacy.touch()
    canonical.touch()

    found = discover_legacy_databases(home=home, canonical_db=canonical)

    assert found == [legacy]


def test_unsupported_database_is_rejected_without_writes(tmp_path: Path):
    source = tmp_path / "unknown.db"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE unrelated (id TEXT)")
    connection.commit()
    connection.close()
    before = source.read_bytes()

    with pytest.raises(ValueError, match="supported history table"):
        inspect_legacy_database(source)

    assert source.read_bytes() == before
