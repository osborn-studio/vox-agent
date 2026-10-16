"""Fail-closed PCM-to-ASR bridge for versioned streaming speech.

The bridge copies bounded process-local PCM into a mode-0600 temporary WAV,
invokes the existing ASR adapter seam, publishes only transcript metadata, and
erases both the snapshot and temporary file before returning.
"""

from __future__ import annotations

import os
import re
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from .asr import AsrAdapter, get_asr_adapter
from .speech_audio import PCM_S16LE, SpeechAudioSnapshot
from .streaming_speech import SpeechStreamBroker

_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_DEFAULT_ADAPTER = "sherpa"
_MAX_TRANSCRIPT_CHARS = 32_000


class SpeechAsrProviderError(RuntimeError):
    """A provider failed or returned output unsafe for the speech contract."""


@dataclass(slots=True)
class _Recognition:
    text: str
    language: str
    confidence: float
    speaker_id: str
    speaker_label: str | None
    audio_start_ms: int
    audio_end_ms: int


@dataclass(slots=True)
class _PublishedState:
    revision: int
    text: str


class SpeechAsrProcessor:
    """Serialize one real ASR adapter and publish stable transcript revisions."""

    def __init__(
        self,
        speech_broker: SpeechStreamBroker,
        *,
        adapter: AsrAdapter | None = None,
        adapter_name: str | None = None,
        min_recognition_ms: int = 250,
        max_states: int | None = None,
    ) -> None:
        resolved_max_states = (
            speech_broker.max_streams if max_states is None else max_states
        )
        for value, name in (
            (min_recognition_ms, "min_recognition_ms"),
            (resolved_max_states, "max_states"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.speech_broker = speech_broker
        self._adapter = adapter
        self._adapter_name = (
            getattr(adapter, "name", None)
            if adapter is not None
            else adapter_name
            or os.environ.get("VOXAGENT_STREAMING_ASR_ADAPTER", _DEFAULT_ADAPTER)
        )
        self.min_recognition_ms = min_recognition_ms
        self.max_states = resolved_max_states
        self._states: dict[str, _PublishedState] = {}
        self._lock = RLock()

    @property
    def adapter_name(self) -> str:
        return self._adapter_name

    def publish_partial(
        self,
        snapshot: SpeechAudioSnapshot,
        resume_token: str,
    ) -> dict | None:
        return self._publish(snapshot, resume_token, final=False)

    def publish_final(
        self,
        snapshot: SpeechAudioSnapshot,
        resume_token: str,
    ) -> dict | None:
        return self._publish(snapshot, resume_token, final=True)

    def clear(self, stream_id: str) -> None:
        with self._lock:
            self._states.pop(stream_id, None)

    def _publish(
        self,
        snapshot: SpeechAudioSnapshot,
        resume_token: str,
        *,
        final: bool,
    ) -> dict | None:
        try:
            with self._lock:
                state = self._states.get(snapshot.stream_id)
                snapshot_span_ms = snapshot.duration_ms - snapshot.audio_start_ms
                if snapshot_span_ms < self.min_recognition_ms and state is None:
                    return None
                context = self.speech_broker.recognition_context(
                    snapshot.stream_id,
                    resume_token,
                )
                recognition = self._recognize(snapshot, context)
                if recognition is None:
                    if final and state is not None:
                        raise SpeechAsrProviderError(
                            "provider returned an empty final after a partial"
                        )
                    return None
                if not final and state is not None and recognition.text == state.text:
                    return None

                revision = (
                    state.revision
                    if final and state is not None and recognition.text == state.text
                    else (state.revision if state is not None else 0) + 1
                )
                event_type = "transcript.final" if final else "transcript.partial"
                event = self.speech_broker.publish_transcript(
                    snapshot.stream_id,
                    resume_token,
                    event_type=event_type,
                    segment_id=f"asr_{snapshot.stream_id}",
                    revision=revision,
                    text=recognition.text,
                    language={
                        "code": recognition.language,
                        "confidence": recognition.confidence,
                    },
                    speaker={
                        "id": recognition.speaker_id,
                        "label": recognition.speaker_label,
                    },
                    audio_start_ms=recognition.audio_start_ms,
                    audio_end_ms=recognition.audio_end_ms,
                    confidence=recognition.confidence,
                    monotonic_ms=snapshot.duration_ms,
                    idempotency_key=(
                        f"asr:{snapshot.stream_id}:{event_type}:{revision}"
                    ),
                )
                if state is None and len(self._states) >= self.max_states:
                    self._states.pop(next(iter(self._states)))
                self._states[snapshot.stream_id] = _PublishedState(
                    revision=revision,
                    text=recognition.text,
                )
                return event
        finally:
            snapshot.wipe()

    def _recognize(self, snapshot: SpeechAudioSnapshot, context: dict) -> _Recognition | None:
        path: Path | None = None
        try:
            adapter = self._adapter
            if adapter is None:
                adapter = get_asr_adapter(self._adapter_name)
                self._adapter = adapter
            path = self._write_temporary_wav(snapshot)
            segments = adapter.transcribe(
                context["sessionId"] or snapshot.stream_id,
                str(path),
                list(context["languageHints"]),
            )
            return _aggregate_segments(
                segments,
                duration_ms=snapshot.duration_ms,
                audio_start_ms=snapshot.audio_start_ms,
                language_hints=context["languageHints"],
            )
        except SpeechAsrProviderError:
            raise
        except Exception as exc:
            raise SpeechAsrProviderError("speech recognition provider failed") from exc
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    @staticmethod
    def _write_temporary_wav(snapshot: SpeechAudioSnapshot) -> Path:
        if snapshot.encoding != PCM_S16LE or snapshot.channels != 1:
            raise SpeechAsrProviderError("unsupported PCM snapshot format")
        handle = tempfile.NamedTemporaryFile(
            prefix="voxagent-speech-",
            suffix=".wav",
            delete=False,
        )
        path = Path(handle.name)
        handle.close()
        try:
            with wave.open(str(path), "wb") as output:
                output.setnchannels(snapshot.channels)
                output.setsampwidth(2)
                output.setframerate(snapshot.sample_rate)
                output.writeframes(snapshot.audio)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path


def _aggregate_segments(
    segments: object,
    *,
    duration_ms: int,
    audio_start_ms: int,
    language_hints: list[str],
) -> _Recognition | None:
    if not isinstance(segments, list):
        raise SpeechAsrProviderError("provider output must be a segment list")
    nonempty: list[dict] = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise SpeechAsrProviderError("provider segment must be an object")
        text = segment.get("textNormalized") or segment.get("textRaw")
        if not isinstance(text, str):
            raise SpeechAsrProviderError("provider segment text must be a string")
        text = text.strip()
        if text:
            nonempty.append({**segment, "_text": text})
    if not nonempty:
        return None

    text = " ".join(segment["_text"] for segment in nonempty)
    if len(text) > _MAX_TRANSCRIPT_CHARS:
        raise SpeechAsrProviderError("provider transcript exceeds the contract limit")
    first = nonempty[0]
    language = first.get("language")
    if not isinstance(language, str) or _LANGUAGE_CODE.fullmatch(language) is None:
        language = next(
            (
                hint
                for hint in language_hints
                if isinstance(hint, str) and _LANGUAGE_CODE.fullmatch(hint)
            ),
            "und",
        )
    confidence = first.get("confidence", 1.0)
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        raise SpeechAsrProviderError("provider confidence is outside the contract")
    speaker_id = first.get("speakerId") or "speaker_1"
    if not isinstance(speaker_id, str) or not 1 <= len(speaker_id) <= 128:
        raise SpeechAsrProviderError("provider speaker id is outside the contract")
    speaker_label = first.get("speakerLabel")
    if speaker_label is not None and (
        not isinstance(speaker_label, str) or not 1 <= len(speaker_label) <= 128
    ):
        raise SpeechAsrProviderError("provider speaker label is outside the contract")
    return _Recognition(
        text=text,
        language=language,
        confidence=float(confidence),
        speaker_id=speaker_id,
        speaker_label=speaker_label,
        audio_start_ms=audio_start_ms,
        audio_end_ms=duration_ms,
    )
