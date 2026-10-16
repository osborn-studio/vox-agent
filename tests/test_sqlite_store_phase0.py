"""Tests for Phase 0 fields in SQLite storage."""

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from memory.sqlite_store import SQLiteStore
from runtime.models.events import OutputMode, RecordingMode, TargetRiskLevel, VoiceEvent


@pytest_asyncio.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "phase0.db"
        s = SQLiteStore(db_path)
        await s.init()
        yield s
        await s.close()


@pytest.mark.asyncio
async def test_history_store_uses_windows_safe_connection_policy(store: SQLiteStore):
    expected = {
        "journal_mode": "wal",
        "busy_timeout": 5000,
        "synchronous": 1,
        "foreign_keys": 1,
    }
    for pragma, value in expected.items():
        cursor = await store._db.execute(f"PRAGMA {pragma}")
        row = await cursor.fetchone()
        assert row[0] == value


@pytest.mark.asyncio
async def test_save_event_persists_phase0_columns(store: SQLiteStore):
    event = VoiceEvent(
        session_id="session-1",
        utterance_id="utterance-1",
        target_app="Cursor",
        target_risk_level=TargetRiskLevel.MEDIUM,
        raw_text="hello world",
        display_text="hello world",
        stt_provider="whispercpp-local",
        stt_model="small-q5_1",
        output_mode=OutputMode.PREVIEW_ONLY,
        confidence=0.84,
    )

    await store.save_event(event)

    cursor = await store._db.execute(
        "SELECT utterance_id, target_risk_level, stt_model, output_mode, confidence "
        "FROM voice_events WHERE id=?",
        (event.id,),
    )
    row = await cursor.fetchone()

    assert row == (
        "utterance-1",
        "medium",
        "small-q5_1",
        "preview_only",
        0.84,
    )


@pytest.mark.asyncio
async def test_search_uses_fts_index_for_prefix_queries(store: SQLiteStore):
    event = VoiceEvent(
        session_id="session-fts",
        utterance_id="utterance-fts",
        target_app="Cursor",
        raw_text="discussed digest compiler",
        display_text="discussed digest compiler",
        polished_text="session digest compiler roadmap",
        stt_provider="whispercpp-local",
        stt_model="base.en-q5_1",
    )

    await store.save_event(event)

    results = await store.search("diges", limit=10)

    assert results
    assert results[0]["id"] == event.id


@pytest.mark.asyncio
async def test_search_empty_query_returns_recent_non_ephemeral_events(store: SQLiteStore):
    normal = VoiceEvent(
        session_id="session-normal",
        utterance_id="utterance-normal",
        target_app="Cursor",
        raw_text="normal result",
        display_text="normal result",
    )
    ephemeral = VoiceEvent(
        session_id="session-ephemeral",
        utterance_id="utterance-ephemeral",
        target_app="Terminal",
        raw_text="ephemeral result",
        display_text="ephemeral result",
        recording_mode=RecordingMode.EPHEMERAL,
    )

    await store.save_event(normal)
    await store.save_event(ephemeral)

    results = await store.search("", limit=10)

    ids = [item["id"] for item in results]
    assert normal.id in ids
    assert ephemeral.id not in ids


@pytest.mark.asyncio
async def test_portable_history_export_is_paged_and_excludes_ephemeral(store: SQLiteStore):
    for index in range(3):
        await store.save_event(
            VoiceEvent(
                id=f"event-{index}",
                created_at=f"2026-07-19T10:00:0{index}+00:00",
                raw_text=f"raw {index}",
                display_text=f"final {index}",
            )
        )
    await store.save_event(
        VoiceEvent(
            id="event-private-ephemeral",
            raw_text="do not migrate",
            display_text="do not migrate",
            recording_mode=RecordingMode.EPHEMERAL,
        )
    )

    first_page = await store.list_portable_history(offset=0, limit=2)
    second_page = await store.list_portable_history(offset=2, limit=2)

    assert [item["id"] for item in first_page] == ["event-2", "event-1"]
    assert [item["id"] for item in second_page] == ["event-0"]
    assert all(item["id"] != "event-private-ephemeral" for item in first_page + second_page)


@pytest.mark.asyncio
async def test_portable_history_import_is_idempotent_and_local_wins(store: SQLiteStore):
    local = VoiceEvent(
        id="shared-event",
        created_at="2026-07-19T10:00:00+00:00",
        raw_text="local raw",
        display_text="local final",
    )
    await store.save_event(local)
    records = [
        {
            "id": "shared-event",
            "created_at": "2026-07-19T10:00:00Z",
            "utterance_id": "remote-utterance",
            "output_id": "remote-output",
            "session_id": "remote-session",
            "raw_text": "remote raw",
            "display_text": "remote final",
            "polished_text": "remote final",
            "stt_provider": "sherpa-sensevoice",
            "stt_model": "sensevoice",
            "llm_provider": "openai-gpt",
            "latency_ms": 35,
            "target_app": "Cursor",
            "target_risk_level": "low",
            "role": "me",
            "recording_mode": "normal",
            "output_mode": "paste",
            "confidence": 0.9,
            "archive_status": "polished",
            "agent": "codex",
        },
        {
            "id": "remote-event",
            "created_at": "2026-07-19T10:01:00Z",
            "utterance_id": "remote-utterance-2",
            "output_id": "remote-output-2",
            "session_id": "remote-session",
            "raw_text": "portable raw",
            "display_text": "portable final",
            "polished_text": "portable final",
            "stt_provider": "sherpa-sensevoice",
            "stt_model": "sensevoice",
            "llm_provider": "openai-gpt",
            "latency_ms": 40,
            "target_app": "Cursor",
            "target_risk_level": "medium",
            "role": "me",
            "recording_mode": "private",
            "output_mode": "preview_only",
            "confidence": 0.8,
            "archive_status": "polished",
            "agent": "codex",
        },
    ]

    first = await store.import_portable_history(records)
    second = await store.import_portable_history(records)
    local_after = next(item for item in await store.search("", limit=10) if item["id"] == "shared-event")

    assert first == {"imported": 1, "skipped": 1}
    assert second == {"imported": 0, "skipped": 2}
    assert local_after["display_text"] == "local final"
    assert await store.count() == 2


@pytest.mark.asyncio
async def test_portable_history_import_rejects_ephemeral_and_invalid_records(store: SQLiteStore):
    with pytest.raises(ValueError, match="ephemeral"):
        await store.import_portable_history(
            [
                {
                    "id": "unsafe-event",
                    "created_at": "2026-07-19T10:01:00Z",
                    "raw_text": "secret",
                    "display_text": "secret",
                    "recording_mode": "ephemeral",
                }
            ]
        )
