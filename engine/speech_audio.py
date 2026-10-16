"""Bounded process-local PCM transport for versioned speech streams.

Raw audio is accepted only after the owning speech stream capability has been
verified. Bytes remain in this private, bounded store until a processor consumes
or a terminal action discards them; they never enter speech events, audit
records, conversation state, analytics, or logs.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Callable

from .streaming_speech import SpeechStreamBroker, StreamTerminal

SPEECH_AUDIO_CHUNK_SCHEMA = "voxagent.speech.audio-chunk"
SPEECH_AUDIO_CHUNK_SCHEMA_VERSION = 1
PCM_S16LE = "pcm_s16le"


class SpeechAudioError(ValueError):
    """Base error for invalid or unavailable speech audio transport."""


class AudioChunkConflict(SpeechAudioError):
    pass


class AudioChunkLimitExceeded(SpeechAudioError):
    pass


@dataclass(slots=True, frozen=True)
class _ChunkReceipt:
    digest: bytes
    ack: dict


@dataclass(slots=True)
class _AudioSession:
    sample_rate: int
    channels: int
    encoding: str
    last_activity_ms: float
    audio: bytearray = field(default_factory=bytearray, repr=False)
    receipts: dict[int, _ChunkReceipt] = field(default_factory=dict, repr=False)
    next_chunk_index: int = 0
    last_snapshot_duration_ms: int = 0
    sealed: bool = False


@dataclass(slots=True)
class SpeechAudioSnapshot:
    """Owned copy of bounded PCM that callers must erase after processing."""

    stream_id: str
    sample_rate: int
    channels: int
    encoding: str
    audio_start_ms: int
    duration_ms: int
    audio: bytearray = field(repr=False)

    def wipe(self) -> None:
        self.audio[:] = b"\x00" * len(self.audio)


class SpeechAudioChunkStore:
    """Capability-scoped, ordered, idempotent PCM chunk buffer."""

    def __init__(
        self,
        speech_broker: SpeechStreamBroker,
        *,
        max_chunk_bytes: int = 256 * 1024,
        max_total_bytes: int = 6 * 1024 * 1024,
        max_duration_ms: int = 60_000,
        max_sessions: int = 8,
        idle_ttl_ms: int = 5 * 60 * 1_000,
        partial_interval_ms: int = 1_000,
        partial_window_ms: int = 8_000,
        clock_ms: Callable[[], float] | None = None,
    ) -> None:
        for value, name in (
            (max_chunk_bytes, "max_chunk_bytes"),
            (max_total_bytes, "max_total_bytes"),
            (max_duration_ms, "max_duration_ms"),
            (max_sessions, "max_sessions"),
            (idle_ttl_ms, "idle_ttl_ms"),
            (partial_interval_ms, "partial_interval_ms"),
            (partial_window_ms, "partial_window_ms"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.speech_broker = speech_broker
        self.max_chunk_bytes = max_chunk_bytes
        self.max_total_bytes = max_total_bytes
        self.max_duration_ms = max_duration_ms
        self.max_sessions = max_sessions
        self.idle_ttl_ms = idle_ttl_ms
        self.partial_interval_ms = partial_interval_ms
        self.partial_window_ms = partial_window_ms
        self._clock_ms = clock_ms or (lambda: time.monotonic() * 1_000)
        self._sessions: dict[str, _AudioSession] = {}
        self._lock = RLock()

    def authorize(self, stream_id: str, resume_token: str) -> None:
        try:
            self.speech_broker.require_active(stream_id, resume_token)
        except StreamTerminal:
            self.discard(stream_id)
            raise

    def append(
        self,
        stream_id: str,
        resume_token: str,
        *,
        chunk_index: int,
        sample_rate: int,
        channels: int,
        encoding: str,
        audio: bytes,
    ) -> dict:
        chunk_index = _bounded_integer(
            chunk_index,
            "chunk_index",
            minimum=0,
        )
        sample_rate = _bounded_integer(
            sample_rate,
            "sample_rate",
            minimum=8_000,
            maximum=48_000,
        )
        channels = _bounded_integer(
            channels,
            "channels",
            minimum=1,
            maximum=1,
        )
        if encoding != PCM_S16LE:
            raise SpeechAudioError("audio encoding must be pcm_s16le")
        if not isinstance(audio, bytes) or not audio:
            raise SpeechAudioError("audio chunk must contain bytes")
        if len(audio) > self.max_chunk_bytes:
            raise AudioChunkLimitExceeded("audio chunk exceeds the byte limit")
        frame_bytes = channels * 2
        if len(audio) % frame_bytes != 0:
            raise SpeechAudioError("audio chunk must contain complete PCM frames")

        digest = hashlib.sha256(audio).digest()
        now_ms = self._clock_ms()
        with self._lock:
            self.authorize(stream_id, resume_token)
            self._prune_locked(now_ms)
            session = self._sessions.get(stream_id)
            if session is None:
                if len(self._sessions) >= self.max_sessions:
                    raise AudioChunkLimitExceeded(
                        "concurrent speech audio capacity reached"
                    )
                session = _AudioSession(
                    sample_rate=sample_rate,
                    channels=channels,
                    encoding=encoding,
                    last_activity_ms=now_ms,
                )
                self._sessions[stream_id] = session
            elif (
                session.sample_rate != sample_rate
                or session.channels != channels
                or session.encoding != encoding
            ):
                raise AudioChunkConflict(
                    "audio format cannot change within a speech stream"
                )
            if session.sealed:
                raise AudioChunkConflict("speech audio is sealed for final recognition")

            receipt = session.receipts.get(chunk_index)
            if receipt is not None:
                if receipt.digest == digest:
                    session.last_activity_ms = now_ms
                    return dict(receipt.ack)
                raise AudioChunkConflict(
                    "audio chunk index was reused for different bytes"
                )
            if chunk_index != session.next_chunk_index:
                raise AudioChunkConflict("audio chunks must arrive in order")

            total_bytes = len(session.audio) + len(audio)
            duration_ms = total_bytes * 1_000 // (
                sample_rate * channels * 2
            )
            if total_bytes > self.max_total_bytes:
                raise AudioChunkLimitExceeded(
                    "speech audio exceeds the total byte limit"
                )
            if duration_ms > self.max_duration_ms:
                raise AudioChunkLimitExceeded(
                    "speech audio exceeds the duration limit"
                )

            session.audio.extend(audio)
            session.next_chunk_index += 1
            session.last_activity_ms = now_ms
            ack = {
                "schema": SPEECH_AUDIO_CHUNK_SCHEMA,
                "schemaVersion": SPEECH_AUDIO_CHUNK_SCHEMA_VERSION,
                "streamId": stream_id,
                "chunkIndex": chunk_index,
                "acceptedBytes": len(audio),
                "totalBytes": total_bytes,
                "durationMs": duration_ms,
                "nextChunkIndex": session.next_chunk_index,
            }
            session.receipts[chunk_index] = _ChunkReceipt(digest=digest, ack=ack)
            return dict(ack)

    def snapshot_if_due(
        self,
        stream_id: str,
        resume_token: str,
    ) -> SpeechAudioSnapshot | None:
        """Copy cumulative PCM after each bounded partial-recognition interval."""

        with self._lock:
            session = self._sessions.get(stream_id)
            if session is None:
                return None
            self.authorize(stream_id, resume_token)
            if session.sealed:
                raise AudioChunkConflict("speech audio is sealed for final recognition")
            duration_ms = self._duration_ms(session)
            if (
                duration_ms < self.partial_interval_ms
                or duration_ms - session.last_snapshot_duration_ms
                < self.partial_interval_ms
            ):
                return None
            session.last_snapshot_duration_ms = duration_ms
            return self._snapshot(
                stream_id,
                session,
                duration_ms,
                window_ms=self.partial_window_ms,
            )

    def seal(
        self,
        stream_id: str,
        resume_token: str,
    ) -> SpeechAudioSnapshot | None:
        """Prevent later appends and return one final owned PCM snapshot."""

        with self._lock:
            session = self._sessions.get(stream_id)
            if session is None:
                return None
            self.authorize(stream_id, resume_token)
            if session.sealed:
                raise AudioChunkConflict("speech audio is already sealed")
            session.sealed = True
            return self._snapshot(stream_id, session, self._duration_ms(session))

    def discard(self, stream_id: str) -> None:
        with self._lock:
            self._discard_locked(stream_id)

    def terminate(
        self,
        stream_id: str,
        operation: Callable[[], dict],
    ) -> dict:
        """Run a broker terminal operation atomically with audio disposal."""

        with self._lock:
            result = operation()
            self._discard_locked(stream_id)
            return result

    def _prune_locked(self, now_ms: float) -> None:
        expired = [
            stream_id
            for stream_id, session in self._sessions.items()
            if now_ms - session.last_activity_ms >= self.idle_ttl_ms
        ]
        for stream_id in expired:
            self._discard_locked(stream_id)

    def _discard_locked(self, stream_id: str) -> None:
        session = self._sessions.pop(stream_id, None)
        if session is not None:
            session.audio[:] = b"\x00" * len(session.audio)

    @staticmethod
    def _duration_ms(session: _AudioSession) -> int:
        return len(session.audio) * 1_000 // (
            session.sample_rate * session.channels * 2
        )

    @staticmethod
    def _snapshot(
        stream_id: str,
        session: _AudioSession,
        duration_ms: int,
        *,
        window_ms: int | None = None,
    ) -> SpeechAudioSnapshot:
        audio = session.audio
        audio_start_ms = 0
        if window_ms is not None and duration_ms > window_ms:
            frame_bytes = session.channels * 2
            window_bytes = (
                session.sample_rate * frame_bytes * window_ms // 1_000
            )
            window_bytes -= window_bytes % frame_bytes
            audio = session.audio[-window_bytes:]
            audio_start_ms = duration_ms - (
                len(audio) * 1_000 // (session.sample_rate * frame_bytes)
            )
        return SpeechAudioSnapshot(
            stream_id=stream_id,
            sample_rate=session.sample_rate,
            channels=session.channels,
            encoding=session.encoding,
            audio_start_ms=audio_start_ms,
            duration_ms=duration_ms,
            audio=bytearray(audio),
        )


def _bounded_integer(
    value: object,
    name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SpeechAudioError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise SpeechAudioError(f"{name} is outside the supported range")
    return value
