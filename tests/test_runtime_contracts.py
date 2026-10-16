"""Tests for the Phase 0 runtime contracts."""

from runtime.models.events import (
    ArchiveStatus,
    EventEnvelope,
    FastPathResult,
    FastPathStatus,
    OutputMode,
    RecordingMode,
    TargetRiskLevel,
    VoiceEvent,
)


def test_voice_event_defaults_match_phase0_contract():
    event = VoiceEvent(raw_text="hello", display_text="hello")

    assert event.utterance_id
    assert event.recording_mode == RecordingMode.NORMAL
    assert event.target_risk_level == TargetRiskLevel.LOW
    assert event.output_mode == OutputMode.PASTE
    assert event.archive_status == ArchiveStatus.QUEUED


def test_fastpath_result_supports_review_flags():
    result = FastPathResult(
        id="evt-1",
        status=FastPathStatus.NEEDS_REVIEW,
        display_text="deploy to prod",
        raw_text="deploy to prod",
        stt_provider="whispercpp-local",
        stt_model="small-q5_1",
        target_app="Terminal",
        target_risk_level=TargetRiskLevel.HIGH,
        should_autopaste=False,
        needs_review=True,
        confidence=0.81,
        dictionary_applied=[],
        latency_ms=420,
    )

    assert result.status == FastPathStatus.NEEDS_REVIEW
    assert result.target_risk_level == TargetRiskLevel.HIGH
    assert result.should_autopaste is False


def test_voice_event_can_store_session_and_output_fields():
    event = VoiceEvent(
        session_id="session-1",
        output_id="output-1",
        raw_text="hello",
        display_text="hello",
    )

    assert event.session_id == "session-1"
    assert event.output_id == "output-1"


def test_event_envelope_carries_session_and_utterance_ids():
    envelope = EventEnvelope(
        event_type="stt.final",
        session_id="session-1",
        utterance_id="utterance-1",
        source="desktop-shell",
        payload={"raw_text": "hello"},
    )

    assert envelope.event_id
    assert envelope.version == 1
    assert envelope.session_id == "session-1"
    assert envelope.payload["raw_text"] == "hello"
