"""Portable Vox Agent migration endpoints stay bounded, idempotent, and secret-free."""

import json
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from apps.desktop import server


class FakeMigrationStore:
    def __init__(self):
        self.imported_batches: list[list[dict]] = []
        self.known_ids: set[str] = set()

    async def list_portable_history(self, *, offset: int, limit: int):
        return [
            {
                "id": f"event-{offset}",
                "created_at": "2026-07-19T10:00:00+00:00",
                "utterance_id": "utterance",
                "output_id": "output",
                "session_id": "session",
                "raw_text": "raw",
                "display_text": "final",
                "polished_text": "final",
                "stt_provider": "sherpa-sensevoice",
                "stt_model": "sensevoice",
                "llm_provider": "openai-gpt",
                "latency_ms": 40,
                "target_app": "Cursor",
                "target_risk_level": "low",
                "role": "me",
                "recording_mode": "normal",
                "output_mode": "paste",
                "confidence": 0.9,
                "archive_status": "polished",
                "agent": "codex",
            }
        ][:limit]

    async def import_portable_history(self, records: list[dict]):
        self.imported_batches.append(records)
        imported = 0
        for record in records:
            if record["id"] in self.known_ids:
                continue
            self.known_ids.add(record["id"])
            imported += 1
        return {"imported": imported, "skipped": len(records) - imported}


def _client(tmp_path, monkeypatch):
    store = FakeMigrationStore()
    terms_path = tmp_path / "terms.json"
    terms_path.write_text(
        json.dumps(
            {
                "version": 1,
                "corrections": {"local": "Local Choice"},
                "preserve": ["Local Choice"],
                "format_rules": {"cn_en_space": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_config", SimpleNamespace(api_token=""))
    monkeypatch.setattr(server, "_corrector", None)
    monkeypatch.setattr(server, "_terms_path", lambda: terms_path)
    return TestClient(server.app), store


def test_history_migration_endpoints_are_paged_and_bounded(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)

    exported = client.get("/v1/migration/history?offset=7&limit=100")
    imported = client.post(
        "/v1/migration/history/import",
        json={"items": exported.json()},
    )
    oversized = client.post(
        "/v1/migration/history/import",
        json={"items": [{}] * 201},
    )

    assert exported.status_code == 200
    assert exported.json()[0]["id"] == "event-7"
    assert imported.status_code == 200
    assert imported.json() == {"imported": 1, "skipped": 0}
    assert len(store.imported_batches) == 1
    assert oversized.status_code == 400


def test_dictionary_import_uses_local_wins_merge_and_rejects_unknown_shape(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    response = client.post(
        "/v1/migration/dictionary/import",
        json={
            "version": 1,
            "corrections": {"local": "Remote Override", "voxagent": "Vox Agent"},
            "preserve": ["Vox Agent", "Local Choice"],
            "format_rules": {"cn_en_space": False},
        },
    )
    invalid = client.post(
        "/v1/migration/dictionary/import",
        json={"corrections": {}, "preserve": [], "secrets": {"token": "no"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported_corrections"] == 1
    assert payload["skipped_corrections"] == 1
    assert payload["dictionary"]["corrections"] == {
        "local": "Local Choice",
        "voxagent": "Vox Agent",
    }
    assert payload["dictionary"]["format_rules"]["cn_en_space"] is True
    assert invalid.status_code == 400


def test_legacy_endpoint_imports_allowlisted_source_copy_only_and_idempotently(
    tmp_path, monkeypatch
):
    client, store = _client(tmp_path, monkeypatch)
    source = tmp_path / "VoxLog" / "history.db"
    source.parent.mkdir()
    connection = sqlite3.connect(source)
    connection.execute(
        """CREATE TABLE recognition_history (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            final_text TEXT NOT NULL,
            status TEXT NOT NULL,
            asr_provider TEXT,
            agent TEXT
        )"""
    )
    connection.execute(
        "INSERT INTO recognition_history VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-api-1",
            "2026-07-01T10:00:00Z",
            "raw",
            "final",
            "completed",
            "qwen",
            "codex",
        ),
    )
    connection.commit()
    connection.close()
    before = source.read_bytes()
    monkeypatch.setattr(server, "_legacy_database_paths", lambda: [source])

    inspected = client.get("/v1/migration/legacy")
    first = client.post("/v1/migration/legacy/import", json={})
    second = client.post("/v1/migration/legacy/import", json={})

    assert inspected.status_code == 200
    assert inspected.json()["records_found"] == 1
    assert first.status_code == 200
    assert first.json()["imported"] == 1
    assert second.json()["imported"] == 0
    assert second.json()["skipped"] == 1
    assert store.imported_batches[0][0]["id"] == "legacy-api-1"
    assert source.read_bytes() == before
