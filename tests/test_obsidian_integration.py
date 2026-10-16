"""Tests for the minimal Obsidian digest export sink."""

from pathlib import Path

import sqlite3

from integrations.obsidian import ObsidianSink, sync_recent


def test_obsidian_sink_exports_project_digest(tmp_path: Path):
    vault = tmp_path / "Vault"
    sink = ObsidianSink(vault)

    result = sink.export_digest(
        scope="project",
        project_key="cursor",
        content="# project_digest\n\ncursor digest body\n",
    )

    note_path = Path(result.note_path)
    assert note_path.exists()
    assert note_path.name == "cursor.md"
    assert "cursor digest body" in note_path.read_text(encoding="utf-8")
    assert result.bytes_written > 0
    assert note_path.relative_to(vault).parts[:2] == ("Vox Agent", "Projects")


def test_recent_sync_targets_selected_vault_with_vox_agent_branding(tmp_path: Path):
    db_path = tmp_path / "voice.db"
    db = sqlite3.connect(db_path)
    db.execute(
        """CREATE TABLE voice_events (
            created_at TEXT, raw_text TEXT, display_text TEXT, polished_text TEXT,
            stt_provider TEXT, target_app TEXT, role TEXT, agent TEXT, recording_mode TEXT
        )"""
    )
    db.execute(
        """INSERT INTO voice_events VALUES (
            datetime('now'), 'raw', 'ship the upgrade', '', 'local', 'Cursor',
            'me', 'codex', 'normal'
        )"""
    )
    db.commit()
    db.close()

    vault = tmp_path / "Selected Vault"
    dates = sync_recent(db_path, vault, days=1)

    assert len(dates) == 1
    note = vault / "Vox Agent" / "Voice Logs" / f"{dates[0]}-voice-log.md"
    assert note.exists()
    content = note.read_text(encoding="utf-8")
    assert "# Vox Agent Voice Log" in content
    assert "tags: [vox-agent, voice-log]" in content
