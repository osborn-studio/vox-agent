"""Sherpa-ONNX ASR adapter (SenseVoice) — the first real local ASR path.

Design (master brief §13 Phase 2): business logic stays behind the
AsrAdapter seam. This module is imported lazily by engine.asr's factory so
the sherpa_onnx package is only required when the adapter is actually used.

API-COMPAT: sherpa-onnx 1.13.x exposes the decoded transcript via
``OfflineStream.result`` (populated in-place by
``OfflineRecognizer.decode_stream``); there is no ``get_result``/
``get_result_all`` method. ``result.text`` holds the transcript and
``result.lang`` a ``<|xx|>``-wrapped language tag.

Model setup:
    .venv/bin/pip install '.[asr]'
    .venv/bin/python scripts/fetch_sherpa_model.py
Model dir override: VOXAGENT_SHERPA_MODEL_DIR.
"""

from __future__ import annotations

import array
import logging
import wave
from pathlib import Path

from runtime.local_asr_paths import (
    SHERPA_BUNDLE,
    VAD_FILE,
    resolve_sherpa_model_dir,
    resolve_sherpa_vad_model,
    vox_agent_models_root,
)

from .vad import (
    SherpaVadSegmenter,
    SpeechRange,
    energy_speech_ranges,
    fixed_window_ranges,
    normalize_speech_ranges,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = vox_agent_models_root() / SHERPA_BUNDLE
DEFAULT_VAD_MODEL = vox_agent_models_root() / VAD_FILE

_INSTALL_HINT = (
    "sherpa-onnx model not found at {model_dir}. Install the runtime with "
    "`.venv/bin/pip install '.[asr]'` and download the SenseVoice/VAD models with "
    "`.venv/bin/python scripts/fetch_sherpa_model.py` (or set VOXAGENT_SHERPA_MODEL_DIR)."
)


def _read_wav(audio_path: str) -> tuple[list[float], int, int]:
    """Return (mono float32 samples in [-1,1], sample_rate, duration_ms)."""
    with wave.open(audio_path, "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise RuntimeError(f"only 16-bit PCM wav supported, got sample width {width}")
    pcm = array.array("h")
    pcm.frombytes(frames)
    if channels > 1:
        pcm = pcm[::channels]  # take first channel
    samples = [s / 32768.0 for s in pcm]
    duration_ms = int(len(samples) / rate * 1000) if rate else 0
    return samples, rate, duration_ms


class SherpaOnnxAsrAdapter:
    """Offline SenseVoice recognition with safe long-recording segmentation."""

    name = "sherpa"

    def __init__(
        self,
        *,
        vad_segmenter=None,
        long_audio_seconds: float = 30.0,
        max_segment_seconds: float = 30.0,
        padding_seconds: float = 0.2,
    ) -> None:
        self._recognizer = None
        self._vad_segmenter = vad_segmenter
        self._long_audio_seconds = long_audio_seconds
        self._max_segment_seconds = max_segment_seconds
        self._padding_seconds = padding_seconds

    def _model_dir(self) -> Path:
        return resolve_sherpa_model_dir()

    def _load(self):
        if self._recognizer is not None:
            return self._recognizer
        model_dir = self._model_dir()
        model_file = model_dir / "model.int8.onnx"
        tokens_file = model_dir / "tokens.txt"
        if not (model_file.exists() and tokens_file.exists()):
            raise RuntimeError(_INSTALL_HINT.format(model_dir=model_dir))
        try:
            import sherpa_onnx
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                "sherpa_onnx runtime unavailable — run `.venv/bin/pip install '.[asr]'`"
            ) from exc
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_file),
            tokens=str(tokens_file),
            use_itn=True,
        )
        return self._recognizer

    def _default_vad_segmenter(self) -> SherpaVadSegmenter:
        return SherpaVadSegmenter(model_path=resolve_sherpa_vad_model())

    @staticmethod
    def _resample(samples: list[float], source_rate: int, target_rate: int = 16000) -> list[float]:
        if source_rate == target_rate:
            return samples
        if source_rate <= 0 or not samples:
            return []
        output_length = max(1, round(len(samples) * target_rate / source_rate))
        if len(samples) == 1:
            return [samples[0]] * output_length
        scale = source_rate / target_rate
        output: list[float] = []
        for index in range(output_length):
            position = min(index * scale, len(samples) - 1)
            left = int(position)
            right = min(left + 1, len(samples) - 1)
            fraction = position - left
            output.append(samples[left] * (1.0 - fraction) + samples[right] * fraction)
        return output

    def _long_audio_ranges(self, samples: list[float]) -> list[SpeechRange]:
        segmenter = self._vad_segmenter or self._default_vad_segmenter()
        try:
            raw_ranges = segmenter.segment(samples, 16000)
        except (RuntimeError, OSError) as exc:
            logger.warning("sherpa VAD unavailable; using conservative fallback: %s", exc)
            raw_ranges = []
        if not raw_ranges:
            raw_ranges = energy_speech_ranges(samples, sample_rate=16000)
        ranges = normalize_speech_ranges(
            raw_ranges,
            total_samples=len(samples),
            sample_rate=16000,
            padding_seconds=self._padding_seconds,
            max_segment_seconds=self._max_segment_seconds,
        )
        if ranges:
            return ranges
        # Never let a VAD false negative silently discard a real meeting.
        return fixed_window_ranges(
            len(samples),
            sample_rate=16000,
            max_segment_seconds=self._max_segment_seconds,
        )

    @staticmethod
    def _payload(result, start_ms: int, end_ms: int) -> dict:
        text = (getattr(result, "text", "") or "").strip()
        language = (getattr(result, "lang", "") or "").strip("<|>") or None
        return {
            "speakerId": "speaker_1",
            "speakerLabel": None,
            "startMs": start_ms,
            "endMs": end_ms,
            "language": language,
            "textRaw": text,
            "textNormalized": text,
            "textTranslated": None,
            "translationTargetLanguage": None,
            "confidence": 1.0,
            "model": "sherpa-onnx-sense-voice",
            "isUserMarked": False,
            "embeddingId": None,
        }

    def transcribe(
        self,
        session_id: str,
        audio_path: str | None,
        language_hints: list[str],
    ) -> list[dict]:
        if not audio_path:
            raise ValueError("sherpa adapter requires an audioPath in the process request body")
        recognizer = self._load()
        if not Path(audio_path).exists():
            raise RuntimeError(f"audio file not found: {audio_path}")
        samples, rate, duration_ms = _read_wav(audio_path)
        if duration_ms <= self._long_audio_seconds * 1000:
            ranges = [SpeechRange(0, len(samples))]
            asr_samples = samples
            asr_rate = rate
        else:
            asr_samples = self._resample(samples, rate)
            asr_rate = 16000
            ranges = self._long_audio_ranges(asr_samples)

        payloads: list[dict] = []
        for item in ranges:
            stream = recognizer.create_stream()
            stream.accept_waveform(asr_rate, asr_samples[item.start_sample : item.end_sample])
            recognizer.decode_stream(stream)
            start_ms = round(item.start_sample / asr_rate * 1000)
            end_ms = min(duration_ms, round(item.end_sample / asr_rate * 1000))
            payloads.append(self._payload(stream.result, start_ms, end_ms))
        return payloads
