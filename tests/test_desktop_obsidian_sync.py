"""Desktop bridge coverage for the user-selected Vox Agent archive sync."""

import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from apps.desktop import server


def _client(tmp_path, monkeypatch) -> TestClient:
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
            datetime('now'), 'raw', 'archive this', '', 'local', 'Cursor',
            'me', 'codex', 'normal'
        )"""
    )
    db.commit()
    db.close()
    monkeypatch.setattr(server, "_config", SimpleNamespace(db_path=db_path, api_token=""))
    return TestClient(server.app)


def test_archive_sync_uses_selected_absolute_vault(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    vault = tmp_path / "My Vault"

    response = client.post(
        "/v1/integrations/obsidian/sync",
        json={"vault_dir": str(vault), "days": 1},
    )

    assert response.status_code == 200
    assert response.json()["exported"] == 1
    assert response.json()["output_dir"] == str(vault / "Vox Agent" / "Voice Logs")


def test_archive_sync_rejects_bool_days_and_relative_paths(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    bool_days = client.post(
        "/v1/integrations/obsidian/sync",
        json={"vault_dir": str(tmp_path / "Vault"), "days": True},
    )
    relative = client.post(
        "/v1/integrations/obsidian/sync",
        json={"vault_dir": "relative/vault", "days": 7},
    )

    assert bool_days.status_code == 400
    assert relative.status_code == 400
