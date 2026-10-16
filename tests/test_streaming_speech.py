"""Contract, reconnect, cancellation, and long-stream tests for speech event v1."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.app_context import create_app_context
from engine.server import create_app
from engine.streaming_speech import (
    InvalidResumeToken,
    ReplayGap,
    SpeechStreamBroker,
    SpeechStreamError,
    StreamCapacityExceeded,
    StreamNotFound,
    StreamTerminal,
)


class FakeMonotonicClock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def __call__(self) -> float:
        self.value += 0.25
        return self.value


def transcript_payload(index: int, *, language: str = "en") -> dict:
    return {
        "segment_id": f"segment-{index}",
        "revision": 1,
        "text": f"segment {index}",
        "language": {"code": language, "confidence": 0.98},
        "speaker": {"id": "speaker-1", "label": "Speaker 1"},
        "audio_start_ms": index * 1_000,
        "audio_end_ms": index * 1_000 + 900,
        "confidence": 0.95,
        "monotonic_ms": index * 1_000,
    }


def test_versioned_partial_final_contract_and_reconnect_replay():
    broker = SpeechStreamBroker(clock_ms=FakeMonotonicClock())
    created = broker.create_stream(session_id="session-1", language_hints=["en", "ja"])

    partial = broker.publish_transcript(
        created["streamId"],
        created["resumeToken"],
        event_type="transcript.partial",
        idempotency_key="partial-1",
        **transcript_payload(0),
    )
    final = broker.publish_transcript(
        created["streamId"],
        created["resumeToken"],
        event_type="transcript.final",
        idempotency_key="final-1",
        **transcript_payload(0),
    )

    assert partial["schema"] == "voxagent.speech.event"
    assert partial["schemaVersion"] == 1
    assert partial["sequence"] == 2
    assert partial["payload"]["language"] == {"code": "en", "confidence": 0.98}
    assert partial["payload"]["speaker"]["id"] == "speaker-1"
    assert final["sequence"] == 3
    assert final["payload"]["segmentId"] == partial["payload"]["segmentId"]

    replay = broker.replay(created["streamId"], created["resumeToken"], after_sequence=1)
    assert [event["sequence"] for event in replay["events"]] == [2, 3]
    assert replay["nextSequence"] == 4
    assert replay["terminal"] is False


def test_idempotency_and_cancellation_are_terminal():
    broker = SpeechStreamBroker(clock_ms=FakeMonotonicClock())
    created = broker.create_stream()
    payload = transcript_payload(0)

    first = broker.publish_transcript(
        created["streamId"],
        created["resumeToken"],
        event_type="transcript.partial",
        idempotency_key="same-event",
        **payload,
    )
    duplicate = broker.publish_transcript(
        created["streamId"],
        created["resumeToken"],
        event_type="transcript.partial",
        idempotency_key="same-event",
        **payload,
    )
    assert duplicate == first

    cancelled = broker.cancel(
        created["streamId"],
        created["resumeToken"],
        reason="user_stopped",
        cancelled_by="local-user",
        idempotency_key="cancel-1",
    )
    assert cancelled["type"] == "stream.cancelled"
    assert broker.cancel(
        created["streamId"],
        created["resumeToken"],
        reason="user_stopped",
        cancelled_by="local-user",
        idempotency_key="cancel-1",
    ) == cancelled

    with pytest.raises(StreamTerminal):
        broker.publish_transcript(
            created["streamId"],
            created["resumeToken"],
            event_type="transcript.final",
            idempotency_key="late-final",
            **payload,
        )


def test_error_contract_rejects_non_boolean_retriable_value():
    broker = SpeechStreamBroker(clock_ms=FakeMonotonicClock())
    created = broker.create_stream()

    with pytest.raises(SpeechStreamError, match="retriable must be a boolean"):
        broker.record_error(
            created["streamId"],
            created["resumeToken"],
            code="provider_timeout",
            message="provider did not answer",
            retriable="false",  # type: ignore[arg-type]
            idempotency_key="error-1",
        )


def test_resume_tokens_are_stream_scoped_and_replay_gaps_fail_closed():
    broker = SpeechStreamBroker(max_events_per_stream=3, clock_ms=FakeMonotonicClock())
    first = broker.create_stream()
    second = broker.create_stream()

    with pytest.raises(InvalidResumeToken):
        broker.replay(first["streamId"], second["resumeToken"], after_sequence=0)

    for index in range(3):
        broker.publish_transcript(
            first["streamId"],
            first["resumeToken"],
            event_type="transcript.partial",
            idempotency_key=f"event-{index}",
            **transcript_payload(index),
        )

    with pytest.raises(ReplayGap) as exc:
        broker.replay(first["streamId"], first["resumeToken"], after_sequence=0)
    assert exc.value.earliest_sequence == 2


def test_stream_capacity_fails_closed_until_terminal_retention_expires():
    clock = FakeMonotonicClock()
    broker = SpeechStreamBroker(
        max_streams=1,
        terminal_retention_ms=10,
        clock_ms=clock,
    )
    first = broker.create_stream()

    with pytest.raises(StreamCapacityExceeded):
        broker.create_stream()

    broker.complete(
        first["streamId"],
        first["resumeToken"],
        reason="done",
        idempotency_key="complete-first",
    )
    clock.value += 20
    second = broker.create_stream()

    assert second["streamId"] != first["streamId"]
    with pytest.raises(StreamNotFound, match="not found"):
        broker.replay(first["streamId"], first["resumeToken"])


def test_eviction_bounds_segment_revision_state_with_replay_window():
    broker = SpeechStreamBroker(max_events_per_stream=3, clock_ms=FakeMonotonicClock())
    created = broker.create_stream()

    for index in range(5):
        broker.publish_transcript(
            created["streamId"],
            created["resumeToken"],
            event_type="transcript.final",
            idempotency_key=f"bounded-{index}",
            **transcript_payload(index),
        )

    stream = broker._streams[created["streamId"]]
    assert len(stream.events) == 3
    assert len(stream.revisions) <= 3
    assert len(stream.finalized_segments) <= 3


def test_declared_long_audio_flow_has_no_sequence_or_segment_loss():
    segment_count = 3_600
    broker = SpeechStreamBroker(
        max_events_per_stream=(segment_count * 2) + 1,
        clock_ms=FakeMonotonicClock(),
    )
    created = broker.create_stream(language_hints=["zh-Hans", "ja", "en"])

    for index in range(segment_count):
        language = ("zh-Hans", "ja", "en")[index % 3]
        payload = transcript_payload(index, language=language)
        broker.publish_transcript(
            created["streamId"],
            created["resumeToken"],
            event_type="transcript.partial",
            idempotency_key=f"partial-{index}",
            **payload,
        )
        broker.publish_transcript(
            created["streamId"],
            created["resumeToken"],
            event_type="transcript.final",
            idempotency_key=f"final-{index}",
            **payload,
        )

    replay = broker.replay(created["streamId"], created["resumeToken"], after_sequence=0)
    sequences = [event["sequence"] for event in replay["events"]]
    finals = [event for event in replay["events"] if event["type"] == "transcript.final"]
    assert sequences == list(range(1, segment_count * 2 + 2))
    assert len(finals) == segment_count
    assert len({event["payload"]["segmentId"] for event in finals}) == segment_count
    assert all(
        left["monotonicMs"] <= right["monotonicMs"]
        for left, right in zip(replay["events"], replay["events"][1:])
    )


def test_http_contract_requires_resume_token_and_preserves_reconnect_cursor(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        bootstrap_admin_token=None,
    )
    with TestClient(create_app(context)) as client:
        created_response = client.post(
            "/v1/speech/streams",
            json={"sessionId": "session-http", "languageHints": ["zh-Hans", "ja", "en"]},
        )
        assert created_response.status_code == 201
        created = created_response.json()
        headers = {"X-VoxAgent-Stream-Token": created["resumeToken"]}

        assert client.get(
            f"/v1/speech/streams/{created['streamId']}/events"
        ).status_code == 401

        partial = client.post(
            f"/v1/speech/streams/{created['streamId']}/partial",
            headers=headers,
            json={
                "segmentId": "segment-http",
                "revision": 1,
                "text": "你好",
                "language": {"code": "zh-Hans", "confidence": 0.99},
                "speaker": {"id": "speaker-a", "label": "A"},
                "audioStartMs": 0,
                "audioEndMs": 800,
                "confidence": 0.97,
                "monotonicMs": 800,
                "idempotencyKey": "http-partial-1",
            },
        )
        assert partial.status_code == 200
        assert partial.json()["sequence"] == 2

        invalid_confidence = client.post(
            f"/v1/speech/streams/{created['streamId']}/partial",
            headers=headers,
            json={
                "segmentId": "segment-invalid",
                "revision": 1,
                "text": "must reject coercion",
                "language": {"code": "en", "confidence": "0.99"},
                "speaker": {"id": "speaker-a", "label": "A"},
                "audioStartMs": 900,
                "audioEndMs": 1_200,
                "confidence": 0.97,
                "monotonicMs": 1_200,
                "idempotencyKey": "invalid-confidence",
            },
        )
        assert invalid_confidence.status_code == 422

        replay = client.get(
            f"/v1/speech/streams/{created['streamId']}/events?afterSequence=1",
            headers=headers,
        )
        assert replay.status_code == 200
        assert [event["sequence"] for event in replay.json()["events"]] == [2]

        cancelled = client.post(
            f"/v1/speech/streams/{created['streamId']}/cancel",
            headers=headers,
            json={
                "reason": "user_stopped",
                "cancelledBy": "local-user",
                "idempotencyKey": "http-cancel-1",
            },
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["type"] == "stream.cancelled"
        audit = context.voice_audit_log.list_events()
        cancelled_audit = next(
            event for event in audit if event["action"] == "speech.stream.cancelled"
        )
        assert cancelled_audit["actorId"] == "local-admin"
        assert cancelled_audit["metadata"]["cancelled_by"] == "local-user"


def test_http_stream_capacity_returns_retryable_rate_limit(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        bootstrap_admin_token=None,
    )
    context.speech_stream_broker = SpeechStreamBroker(max_streams=1)

    with TestClient(create_app(context)) as client:
        first = client.post("/v1/speech/streams", json={})
        blocked = client.post("/v1/speech/streams", json={})

    assert first.status_code == 201
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"
