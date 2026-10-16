"""Versioned, reconnectable streaming speech event broker.

The broker owns protocol ordering and replay semantics; ASR adapters only submit
partial/final transcript revisions. Resume tokens are stored as hashes, transcript
events never contain raw audio, and terminal streams reject late writes.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Callable
from uuid import uuid4

SPEECH_EVENT_SCHEMA = "voxagent.speech.event"
SPEECH_EVENT_SCHEMA_VERSION = 1
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_TRANSCRIPT_EVENTS = {"transcript.partial", "transcript.final"}
_TERMINAL_EVENTS = {"stream.cancelled", "stream.completed", "stream.error"}


class SpeechStreamError(ValueError):
    """Base protocol error."""


class StreamNotFound(SpeechStreamError):
    pass


class InvalidResumeToken(SpeechStreamError):
    pass


class InvalidCursor(SpeechStreamError):
    pass


class ReplayGap(SpeechStreamError):
    def __init__(self, earliest_sequence: int, next_sequence: int) -> None:
        self.earliest_sequence = earliest_sequence
        self.next_sequence = next_sequence
        super().__init__(
            f"replay cursor predates retained events; earliest={earliest_sequence}"
        )


class StreamTerminal(SpeechStreamError):
    pass


class StreamCapacityExceeded(SpeechStreamError):
    pass


class RevisionConflict(SpeechStreamError):
    pass


class SourceSegmentNotFound(SpeechStreamError):
    pass


class SourceLanguageMismatch(SpeechStreamError):
    pass


@dataclass(slots=True)
class _StoredEvent:
    event: dict
    idempotency_key: str | None
    segment_id: str | None


@dataclass(slots=True)
class _SpeechStream:
    stream_id: str
    session_id: str | None
    token_digest: bytes
    started_clock_ms: float
    last_activity_clock_ms: float
    language_hints: list[str]
    max_events: int
    events: deque[_StoredEvent] = field(default_factory=deque)
    idempotency: dict[str, dict] = field(default_factory=dict)
    revisions: dict[str, int] = field(default_factory=dict)
    segment_event_counts: dict[str, int] = field(default_factory=dict)
    finalized_segments: set[str] = field(default_factory=set)
    next_sequence: int = 1
    last_monotonic_ms: int = 0
    terminal_event: dict | None = None
    terminal_clock_ms: float | None = None


class SpeechStreamBroker:
    """Thread-safe in-memory speech stream protocol authority."""

    def __init__(
        self,
        *,
        max_events_per_stream: int = 10_000,
        max_streams: int = 32,
        terminal_retention_ms: int = 60 * 60 * 1_000,
        active_idle_ttl_ms: int = 8 * 60 * 60 * 1_000,
        clock_ms: Callable[[], float] | None = None,
    ) -> None:
        if max_events_per_stream < 2:
            raise ValueError("max_events_per_stream must be at least 2")
        if max_streams < 1:
            raise ValueError("max_streams must be positive")
        if terminal_retention_ms < 1 or active_idle_ttl_ms < 1:
            raise ValueError("stream retention windows must be positive")
        self.max_events_per_stream = max_events_per_stream
        self.max_streams = max_streams
        self.terminal_retention_ms = terminal_retention_ms
        self.active_idle_ttl_ms = active_idle_ttl_ms
        self._clock_ms = clock_ms or (lambda: time.monotonic() * 1_000)
        self._streams: dict[str, _SpeechStream] = {}
        self._lock = RLock()

    def create_stream(
        self,
        *,
        session_id: str | None = None,
        language_hints: list[str] | None = None,
    ) -> dict:
        session_id = _optional_text(session_id, "session_id", max_length=128)
        hints = list(language_hints or [])
        if len(hints) > 8:
            raise SpeechStreamError("language_hints supports at most 8 languages")
        for language in hints:
            _validate_language(language)

        with self._lock:
            now_clock_ms = self._clock_ms()
            self._prune_expired_streams_locked(now_clock_ms)
            if len(self._streams) >= self.max_streams:
                raise StreamCapacityExceeded(
                    f"speech stream capacity reached ({self.max_streams})"
                )
            token = secrets.token_urlsafe(32)
            stream_id = f"speech_{uuid4().hex[:16]}"
            stream = _SpeechStream(
                stream_id=stream_id,
                session_id=session_id,
                token_digest=_token_digest(token),
                started_clock_ms=now_clock_ms,
                last_activity_clock_ms=now_clock_ms,
                language_hints=hints,
                max_events=self.max_events_per_stream,
            )
            self._streams[stream_id] = stream
            self._append_locked(
                stream,
                event_type="stream.started",
                payload={"languageHints": hints},
                monotonic_ms=0,
                idempotency_key="stream.started",
            )
        return {
            "streamId": stream_id,
            "resumeToken": token,
            "schema": SPEECH_EVENT_SCHEMA,
            "schemaVersion": SPEECH_EVENT_SCHEMA_VERSION,
            "nextSequence": stream.next_sequence,
        }

    def publish_transcript(
        self,
        stream_id: str,
        resume_token: str,
        *,
        event_type: str,
        segment_id: str,
        revision: int,
        text: str,
        language: dict,
        speaker: dict,
        audio_start_ms: int,
        audio_end_ms: int,
        confidence: float,
        monotonic_ms: int,
        idempotency_key: str,
    ) -> dict:
        if event_type not in _TRANSCRIPT_EVENTS:
            raise SpeechStreamError(f"unsupported transcript event type: {event_type}")
        idempotency_key = _required_text(
            idempotency_key, "idempotency_key", max_length=160
        )
        segment_id = _required_text(segment_id, "segment_id", max_length=128)
        text = _required_text(text, "text", max_length=32_000)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise SpeechStreamError("revision must be a positive integer")
        _validate_milliseconds(audio_start_ms, "audio_start_ms")
        _validate_milliseconds(audio_end_ms, "audio_end_ms")
        if audio_end_ms < audio_start_ms:
            raise SpeechStreamError("audio_end_ms must be >= audio_start_ms")
        _validate_milliseconds(monotonic_ms, "monotonic_ms")
        _validate_confidence(confidence, "confidence")
        language_payload = _validate_language_payload(language)
        speaker_payload = _validate_speaker(speaker)

        with self._lock:
            stream = self._authenticated_stream(stream_id, resume_token)
            stream.last_activity_clock_ms = self._clock_ms()
            duplicate = stream.idempotency.get(idempotency_key)
            if duplicate is not None:
                return duplicate
            if stream.terminal_event is not None:
                raise StreamTerminal(f"stream is terminal: {stream.terminal_event['type']}")
            previous_revision = stream.revisions.get(segment_id, 0)
            if segment_id in stream.finalized_segments:
                raise RevisionConflict("finalized transcript segments cannot be revised")
            if event_type == "transcript.partial" and revision <= previous_revision:
                raise RevisionConflict("partial revision must increase monotonically")
            if event_type == "transcript.final" and revision < previous_revision:
                raise RevisionConflict("final revision cannot precede the latest partial")

            event = self._append_locked(
                stream,
                event_type=event_type,
                payload={
                    "segmentId": segment_id,
                    "revision": revision,
                    "text": text,
                    "language": language_payload,
                    "speaker": speaker_payload,
                    "audioStartMs": audio_start_ms,
                    "audioEndMs": audio_end_ms,
                    "confidence": confidence,
                },
                monotonic_ms=monotonic_ms,
                idempotency_key=idempotency_key,
            )
            stream.revisions[segment_id] = max(revision, previous_revision)
            if event_type == "transcript.final":
                stream.finalized_segments.add(segment_id)
            return event

    def cancel(
        self,
        stream_id: str,
        resume_token: str,
        *,
        reason: str,
        cancelled_by: str,
        idempotency_key: str,
    ) -> dict:
        reason = _required_text(reason, "reason", max_length=256)
        cancelled_by = _required_text(cancelled_by, "cancelled_by", max_length=128)
        return self._terminate(
            stream_id,
            resume_token,
            event_type="stream.cancelled",
            payload={"reason": reason, "cancelledBy": cancelled_by},
            idempotency_key=idempotency_key,
        )

    def complete(
        self,
        stream_id: str,
        resume_token: str,
        *,
        reason: str,
        idempotency_key: str,
    ) -> dict:
        reason = _required_text(reason, "reason", max_length=256)
        return self._terminate(
            stream_id,
            resume_token,
            event_type="stream.completed",
            payload={"reason": reason},
            idempotency_key=idempotency_key,
        )

    def record_error(
        self,
        stream_id: str,
        resume_token: str,
        *,
        code: str,
        message: str,
        retriable: bool,
        idempotency_key: str,
    ) -> dict:
        if not isinstance(retriable, bool):
            raise SpeechStreamError("retriable must be a boolean")
        return self._terminate(
            stream_id,
            resume_token,
            event_type="stream.error",
            payload={
                "code": _required_text(code, "code", max_length=80),
                "message": _required_text(message, "message", max_length=512),
                "retriable": retriable,
            },
            idempotency_key=idempotency_key,
        )

    def replay(
        self,
        stream_id: str,
        resume_token: str,
        *,
        after_sequence: int = 0,
    ) -> dict:
        if not isinstance(after_sequence, int) or isinstance(after_sequence, bool):
            raise InvalidCursor("after_sequence must be an integer")
        if after_sequence < 0:
            raise InvalidCursor("after_sequence must not be negative")
        with self._lock:
            stream = self._authenticated_stream(stream_id, resume_token)
            stream.last_activity_clock_ms = self._clock_ms()
            latest_sequence = stream.next_sequence - 1
            if after_sequence > latest_sequence:
                raise InvalidCursor("after_sequence is ahead of the stream")
            earliest_sequence = stream.events[0].event["sequence"]
            if after_sequence < earliest_sequence - 1:
                raise ReplayGap(earliest_sequence, stream.next_sequence)
            return {
                "streamId": stream.stream_id,
                "events": [
                    stored.event
                    for stored in stream.events
                    if stored.event["sequence"] > after_sequence
                ],
                "nextSequence": stream.next_sequence,
                "terminal": stream.terminal_event is not None,
            }

    def require_active(self, stream_id: str, resume_token: str) -> None:
        """Authenticate a stream capability without relying on replay retention."""

        with self._lock:
            stream = self._authenticated_stream(stream_id, resume_token)
            if stream.terminal_event is not None:
                raise StreamTerminal(
                    f"stream is terminal: {stream.terminal_event['type']}"
                )
            stream.last_activity_clock_ms = self._clock_ms()

    def recognition_context(self, stream_id: str, resume_token: str) -> dict:
        """Return only the metadata an authenticated ASR adapter may consume."""

        with self._lock:
            stream = self._authenticated_stream(stream_id, resume_token)
            if stream.terminal_event is not None:
                raise StreamTerminal(
                    f"stream is terminal: {stream.terminal_event['type']}"
                )
            stream.last_activity_clock_ms = self._clock_ms()
            return {
                "sessionId": stream.session_id,
                "languageHints": list(stream.language_hints),
            }

    def resolve_final_segments(
        self,
        stream_id: str,
        resume_token: str,
        *,
        references: list[dict],
        source_language: str,
    ) -> list[dict]:
        """Resolve authenticated final transcript references for translation."""

        _validate_language(source_language)
        if not isinstance(references, list) or not 1 <= len(references) <= 8:
            raise SpeechStreamError("references must contain between 1 and 8 items")
        normalized = [_validate_source_reference(reference) for reference in references]
        with self._lock:
            stream = self._authenticated_stream(stream_id, resume_token)
            stream.last_activity_clock_ms = self._clock_ms()
            resolved = []
            for reference in normalized:
                if reference["streamId"] != stream.stream_id:
                    raise SourceSegmentNotFound("translation source stream does not match")
                event = next(
                    (
                        stored.event
                        for stored in reversed(stream.events)
                        if stored.event["type"] == "transcript.final"
                        and stored.event["payload"]["segmentId"] == reference["segmentId"]
                        and stored.event["payload"]["revision"] == reference["revision"]
                    ),
                    None,
                )
                if event is None:
                    raise SourceSegmentNotFound(
                        "final translation source segment is unavailable"
                    )
                payload = event["payload"]
                if payload["language"]["code"] != source_language:
                    raise SourceLanguageMismatch(
                        "translation source language does not match the final segment"
                    )
                resolved.append(
                    {
                        **reference,
                        "text": payload["text"],
                        "languageConfidence": payload["language"]["confidence"],
                        "audioStartMs": payload["audioStartMs"],
                        "audioEndMs": payload["audioEndMs"],
                    }
                )
            return resolved

    def _terminate(
        self,
        stream_id: str,
        resume_token: str,
        *,
        event_type: str,
        payload: dict,
        idempotency_key: str,
    ) -> dict:
        idempotency_key = _required_text(
            idempotency_key, "idempotency_key", max_length=160
        )
        with self._lock:
            stream = self._authenticated_stream(stream_id, resume_token)
            duplicate = stream.idempotency.get(idempotency_key)
            if duplicate is not None:
                return duplicate
            if stream.terminal_event is not None:
                raise StreamTerminal(f"stream is terminal: {stream.terminal_event['type']}")
            event = self._append_locked(
                stream,
                event_type=event_type,
                payload=payload,
                monotonic_ms=self._relative_clock_ms(stream),
                idempotency_key=idempotency_key,
            )
            stream.terminal_event = event
            stream.terminal_clock_ms = stream.last_activity_clock_ms
            return event

    def _append_locked(
        self,
        stream: _SpeechStream,
        *,
        event_type: str,
        payload: dict,
        monotonic_ms: int,
        idempotency_key: str | None,
    ) -> dict:
        if monotonic_ms < stream.last_monotonic_ms:
            raise SpeechStreamError("monotonic_ms cannot move backwards")
        event = {
            "schema": SPEECH_EVENT_SCHEMA,
            "schemaVersion": SPEECH_EVENT_SCHEMA_VERSION,
            "eventId": f"speech_event_{uuid4().hex[:16]}",
            "streamId": stream.stream_id,
            "sessionId": stream.session_id,
            "sequence": stream.next_sequence,
            "type": event_type,
            "monotonicMs": monotonic_ms,
            "emittedAt": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        stream.next_sequence += 1
        stream.last_monotonic_ms = monotonic_ms
        segment_id = payload.get("segmentId") if event_type in _TRANSCRIPT_EVENTS else None
        stored = _StoredEvent(
            event=event,
            idempotency_key=idempotency_key,
            segment_id=segment_id,
        )
        stream.events.append(stored)
        stream.last_activity_clock_ms = self._clock_ms()
        if idempotency_key is not None:
            stream.idempotency[idempotency_key] = event
        if segment_id is not None:
            stream.segment_event_counts[segment_id] = (
                stream.segment_event_counts.get(segment_id, 0) + 1
            )
        while len(stream.events) > stream.max_events:
            evicted = stream.events.popleft()
            if evicted.idempotency_key is not None:
                stream.idempotency.pop(evicted.idempotency_key, None)
            if evicted.segment_id is not None:
                remaining = stream.segment_event_counts[evicted.segment_id] - 1
                if remaining <= 0:
                    stream.segment_event_counts.pop(evicted.segment_id, None)
                    stream.revisions.pop(evicted.segment_id, None)
                    stream.finalized_segments.discard(evicted.segment_id)
                else:
                    stream.segment_event_counts[evicted.segment_id] = remaining
        return event

    def _prune_expired_streams_locked(self, now_clock_ms: float) -> None:
        expired = []
        for stream_id, stream in self._streams.items():
            if stream.terminal_clock_ms is not None:
                age_ms = now_clock_ms - stream.terminal_clock_ms
                if age_ms >= self.terminal_retention_ms:
                    expired.append(stream_id)
            elif now_clock_ms - stream.last_activity_clock_ms >= self.active_idle_ttl_ms:
                expired.append(stream_id)
        for stream_id in expired:
            self._streams.pop(stream_id, None)

    def _authenticated_stream(self, stream_id: str, resume_token: str) -> _SpeechStream:
        stream = self._streams.get(stream_id)
        if stream is None:
            raise StreamNotFound("speech stream not found")
        supplied = _token_digest(resume_token or "")
        if not hmac.compare_digest(stream.token_digest, supplied):
            raise InvalidResumeToken("invalid speech stream resume token")
        return stream

    def _relative_clock_ms(self, stream: _SpeechStream) -> int:
        return max(stream.last_monotonic_ms, int(self._clock_ms() - stream.started_clock_ms))


def _token_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _required_text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpeechStreamError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise SpeechStreamError(f"{field_name} exceeds {max_length} characters")
    return normalized


def _optional_text(value: object, field_name: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name, max_length=max_length)


def _validate_language(value: object) -> str:
    if not isinstance(value, str) or not _LANGUAGE_RE.fullmatch(value):
        raise SpeechStreamError("language must be a BCP 47 tag")
    return value


def _validate_language_payload(value: object) -> dict:
    if not isinstance(value, dict):
        raise SpeechStreamError("language must be an object")
    return {
        "code": _validate_language(value.get("code")),
        "confidence": _validate_confidence(value.get("confidence"), "language.confidence"),
    }


def _validate_speaker(value: object) -> dict:
    if not isinstance(value, dict):
        raise SpeechStreamError("speaker must be an object")
    return {
        "id": _required_text(value.get("id"), "speaker.id", max_length=128),
        "label": _optional_text(value.get("label"), "speaker.label", max_length=128),
    }


def _validate_source_reference(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"streamId", "segmentId", "revision"}:
        raise SpeechStreamError("translation source reference does not match the contract")
    revision = value["revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise SpeechStreamError("translation source revision must be a positive integer")
    return {
        "streamId": _required_text(value["streamId"], "streamId", max_length=128),
        "segmentId": _required_text(value["segmentId"], "segmentId", max_length=128),
        "revision": revision,
    }


def _validate_confidence(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeechStreamError(f"{field_name} must be numeric")
    result = float(value)
    if result < 0 or result > 1:
        raise SpeechStreamError(f"{field_name} must be between 0 and 1")
    return result


def _validate_milliseconds(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SpeechStreamError(f"{field_name} must be a non-negative integer")
    return value
