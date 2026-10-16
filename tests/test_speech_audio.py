"""Bounded authenticated PCM transport tests for speech stream v1."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from engine.app_context import create_app_context
from engine.server import create_app
from engine.speech_audio import (
    AudioChunkConflict,
    AudioChunkLimitExceeded,
    SpeechAudioChunkStore,
)
from engine.streaming_speech import SpeechStreamBroker, StreamTerminal


def _chunk_headers(token: str, *, index: int = 0) -> dict[str, str]:
    return {
        "Content-Type": "application/octet-stream",
        "X-VoxAgent-Stream-Token": token,
        "X-VoxAgent-Audio-Chunk-Index": str(index),
        "X-VoxAgent-Audio-Sample-Rate": "16000",
        "X-VoxAgent-Audio-Channels": "1",
        "X-VoxAgent-Audio-Encoding": "pcm_s16le",
    }


def test_chunk_store_is_ordered_idempotent_bounded_and_terminal() -> None:
    broker = SpeechStreamBroker()
    created = broker.create_stream(session_id="conversation-1", language_hints=["en"])
    store = SpeechAudioChunkStore(
        broker,
        max_chunk_bytes=8,
        max_total_bytes=12,
        max_duration_ms=1_000,
    )
    first_bytes = b"\x01\x00\x02\x00"

    first = store.append(
        created["streamId"],
        created["resumeToken"],
        chunk_index=0,
        sample_rate=16_000,
        channels=1,
        encoding="pcm_s16le",
        audio=first_bytes,
    )
    duplicate = store.append(
        created["streamId"],
        created["resumeToken"],
        chunk_index=0,
        sample_rate=16_000,
        channels=1,
        encoding="pcm_s16le",
        audio=first_bytes,
    )

    assert duplicate == first
    assert first == {
        "schema": "voxagent.speech.audio-chunk",
        "schemaVersion": 1,
        "streamId": created["streamId"],
        "chunkIndex": 0,
        "acceptedBytes": 4,
        "totalBytes": 4,
        "durationMs": 0,
        "nextChunkIndex": 1,
    }
    with pytest.raises(AudioChunkConflict):
        store.append(
            created["streamId"],
            created["resumeToken"],
            chunk_index=0,
            sample_rate=16_000,
            channels=1,
            encoding="pcm_s16le",
            audio=b"\x03\x00\x04\x00",
        )
    with pytest.raises(AudioChunkConflict):
        store.append(
            created["streamId"],
            created["resumeToken"],
            chunk_index=2,
            sample_rate=16_000,
            channels=1,
            encoding="pcm_s16le",
            audio=first_bytes,
        )
    with pytest.raises(AudioChunkLimitExceeded):
        store.append(
            created["streamId"],
            created["resumeToken"],
            chunk_index=1,
            sample_rate=16_000,
            channels=1,
            encoding="pcm_s16le",
            audio=b"\x00" * 10,
        )

    cancelled = store.terminate(
        created["streamId"],
        lambda: broker.cancel(
            created["streamId"],
            created["resumeToken"],
            reason="test",
            cancelled_by="test",
            idempotency_key="cancel-test",
        ),
    )
    duplicate_cancel = store.terminate(
        created["streamId"],
        lambda: broker.cancel(
            created["streamId"],
            created["resumeToken"],
            reason="test",
            cancelled_by="test",
            idempotency_key="cancel-test",
        ),
    )
    assert duplicate_cancel == cancelled
    with pytest.raises(StreamTerminal):
        store.append(
            created["streamId"],
            created["resumeToken"],
            chunk_index=1,
            sample_rate=16_000,
            channels=1,
            encoding="pcm_s16le",
            audio=first_bytes,
        )


def test_chunk_store_snapshots_on_interval_and_seals_against_late_audio() -> None:
    broker = SpeechStreamBroker()
    created = broker.create_stream(language_hints=["en"])
    store = SpeechAudioChunkStore(
        broker,
        partial_interval_ms=1_000,
    )
    half_second = b"\x01\x00" * 8_000
    stream_id = created["streamId"]
    token = created["resumeToken"]

    store.append(
        stream_id,
        token,
        chunk_index=0,
        sample_rate=16_000,
        channels=1,
        encoding="pcm_s16le",
        audio=half_second,
    )
    assert store.snapshot_if_due(stream_id, token) is None
    store.append(
        stream_id,
        token,
        chunk_index=1,
        sample_rate=16_000,
        channels=1,
        encoding="pcm_s16le",
        audio=half_second,
    )
    partial = store.snapshot_if_due(stream_id, token)
    assert partial is not None
    assert partial.duration_ms == 1_000
    assert partial.audio_start_ms == 0
    assert partial.audio == half_second * 2
    assert store.snapshot_if_due(stream_id, token) is None

    final = store.seal(stream_id, token)
    assert final is not None
    assert final.duration_ms == 1_000
    with pytest.raises(AudioChunkConflict, match="sealed"):
        store.append(
            stream_id,
            token,
            chunk_index=2,
            sample_rate=16_000,
            channels=1,
            encoding="pcm_s16le",
            audio=b"\x00\x00",
        )

    partial.wipe()
    final.wipe()
    assert not any(partial.audio)
    assert not any(final.audio)
    store.discard(stream_id)


def test_partial_snapshot_is_a_bounded_rolling_window() -> None:
    broker = SpeechStreamBroker()
    created = broker.create_stream()
    store = SpeechAudioChunkStore(
        broker,
        partial_interval_ms=1_000,
        partial_window_ms=1_000,
    )
    first_second = b"\x01\x00" * 16_000
    second_second = b"\x02\x00" * 16_000
    for index, audio in enumerate((first_second, second_second)):
        store.append(
            created["streamId"],
            created["resumeToken"],
            chunk_index=index,
            sample_rate=16_000,
            channels=1,
            encoding="pcm_s16le",
            audio=audio,
        )
        snapshot = store.snapshot_if_due(
            created["streamId"],
            created["resumeToken"],
        )
    assert snapshot is not None
    assert snapshot.audio_start_ms == 1_000
    assert snapshot.duration_ms == 2_000
    assert snapshot.audio == second_second
    snapshot.wipe()
    store.discard(created["streamId"])


def test_http_audio_chunk_requires_principal_and_stream_capability(tmp_path) -> None:
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        edition="enterprise",
        bootstrap_admin_token="enterprise-admin-token-with-entropy",
    )
    principal = {"Authorization": "Bearer enterprise-admin-token-with-entropy"}
    with TestClient(create_app(context)) as client:
        created_response = client.post(
            "/v1/speech/streams",
            headers=principal,
            json={"sessionId": "conversation-1", "languageHints": ["en"]},
        )
        assert created_response.status_code == 201
        created = created_response.json()
        path = f"/v1/speech/streams/{created['streamId']}/audio/chunks"
        audio = b"private audio text"

        assert client.post(
            path,
            headers=_chunk_headers(created["resumeToken"]),
            content=audio,
        ).status_code == 401
        assert client.post(
            path,
            headers={**principal, **_chunk_headers("wrong-stream-token-with-entropy")},
            content=audio,
        ).status_code == 401

        accepted = client.post(
            path,
            headers={**principal, **_chunk_headers(created["resumeToken"])},
            content=audio,
        )
        assert accepted.status_code == 200
        assert accepted.json()["acceptedBytes"] == len(audio)
        assert accepted.json()["nextChunkIndex"] == 1

        completed = client.post(
            f"/v1/speech/streams/{created['streamId']}/complete",
            headers={
                **principal,
                "X-VoxAgent-Stream-Token": created["resumeToken"],
            },
            json={"reason": "input_finished", "idempotencyKey": "complete-1"},
        )
        assert completed.status_code == 200
        assert completed.json()["type"] == "stream.completed"
        duplicate_completed = client.post(
            f"/v1/speech/streams/{created['streamId']}/complete",
            headers={
                **principal,
                "X-VoxAgent-Stream-Token": created["resumeToken"],
            },
            json={"reason": "input_finished", "idempotencyKey": "complete-1"},
        )
        assert duplicate_completed.status_code == 200
        assert duplicate_completed.json() == completed.json()
        assert client.post(
            path,
            headers={
                **principal,
                **_chunk_headers(created["resumeToken"], index=1),
            },
            content=audio,
        ).status_code == 409

    audit = json.dumps(context.voice_audit_log.list_events())
    assert "speech.audio.chunk" not in audit
    assert "private audio" not in audit


def test_http_audio_chunk_rejects_unsafe_format_order_and_size(tmp_path) -> None:
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        bootstrap_admin_token=None,
    )
    with TestClient(create_app(context)) as client:
        created = client.post("/v1/speech/streams", json={}).json()
        path = f"/v1/speech/streams/{created['streamId']}/audio/chunks"
        headers = _chunk_headers(created["resumeToken"])

        wrong_type = client.post(
            path,
            headers={**headers, "Content-Type": "audio/wav"},
            content=b"\x00\x00",
        )
        assert wrong_type.status_code == 415

        odd_frame = client.post(path, headers=headers, content=b"\x00")
        assert odd_frame.status_code == 400

        out_of_order = client.post(
            path,
            headers={
                **headers,
                "X-VoxAgent-Audio-Chunk-Index": "2",
            },
            content=b"\x00\x00",
        )
        assert out_of_order.status_code == 409

        oversized = client.post(
            path,
            headers=headers,
            content=b"\x00" * (256 * 1024 + 2),
        )
        assert oversized.status_code == 413
