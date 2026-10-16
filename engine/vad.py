"""Voice activity segmentation for long, offline recordings.

The sherpa wrapper follows the upstream Python API: feed 16 kHz windows into
``VoiceActivityDetector``, flush at EOF, and drain ``SpeechSegment`` objects.
Range normalization and conservative fallbacks are kept independent from the
model so they remain deterministic and testable without native dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, order=True)
class SpeechRange:
    """Half-open sample range in the audio passed to a segmenter."""

    start_sample: int
    end_sample: int


class SherpaVadSegmenter:
    """Silero VAD through sherpa-onnx's offline Python API."""

    def __init__(
        self,
        model_path: Path,
        *,
        threshold: float = 0.35,
        min_silence_seconds: float = 0.4,
        min_speech_seconds: float = 0.2,
        max_speech_seconds: float = 30.0,
        window_size: int = 512,
    ) -> None:
        self.model_path = model_path
        self.threshold = threshold
        self.min_silence_seconds = min_silence_seconds
        self.min_speech_seconds = min_speech_seconds
        self.max_speech_seconds = max_speech_seconds
        self.window_size = window_size

    def segment(self, samples: list[float], sample_rate: int) -> list[SpeechRange]:
        if not self.model_path.is_file():
            raise RuntimeError(f"sherpa VAD model not found: {self.model_path}")
        if sample_rate != 16000:
            raise ValueError(f"sherpa Silero VAD requires 16 kHz audio, got {sample_rate} Hz")
        try:
            import sherpa_onnx
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                "sherpa_onnx VAD runtime is unavailable; install the `asr` extra"
            ) from exc

        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(self.model_path)
        config.silero_vad.threshold = self.threshold
        config.silero_vad.min_silence_duration = self.min_silence_seconds
        config.silero_vad.min_speech_duration = self.min_speech_seconds
        config.silero_vad.max_speech_duration = self.max_speech_seconds
        config.silero_vad.window_size = self.window_size
        config.sample_rate = sample_rate
        config.num_threads = 1
        config.provider = "cpu"
        config.debug = False
        if hasattr(config, "validate") and not config.validate():
            raise RuntimeError("invalid sherpa VAD configuration")

        detector = sherpa_onnx.VoiceActivityDetector(
            config,
            buffer_size_in_seconds=max(60.0, len(samples) / sample_rate + 5.0),
        )
        for offset in range(0, len(samples), self.window_size):
            detector.accept_waveform(samples[offset : offset + self.window_size])
        detector.flush()

        ranges: list[SpeechRange] = []
        while not detector.empty:
            speech = detector.front
            start = int(speech.start)
            ranges.append(SpeechRange(start, start + len(speech.samples)))
            detector.pop()
        return ranges


def normalize_speech_ranges(
    ranges: list[SpeechRange],
    *,
    total_samples: int,
    sample_rate: int,
    padding_seconds: float = 0.2,
    merge_gap_seconds: float = 0.25,
    max_segment_seconds: float = 30.0,
) -> list[SpeechRange]:
    """Clip, pad, merge, and cap model ranges for bounded ASR calls."""
    if total_samples <= 0 or sample_rate <= 0:
        return []
    padding = max(0, round(padding_seconds * sample_rate))
    merge_gap = max(0, round(merge_gap_seconds * sample_rate))
    max_samples = max(1, round(max_segment_seconds * sample_rate))

    cleaned = sorted(
        SpeechRange(
            max(0, item.start_sample - padding),
            min(total_samples, item.end_sample + padding),
        )
        for item in ranges
        if item.end_sample > item.start_sample
        and item.end_sample > 0
        and item.start_sample < total_samples
    )
    merged: list[SpeechRange] = []
    for item in cleaned:
        if item.end_sample <= item.start_sample:
            continue
        if merged and item.start_sample <= merged[-1].end_sample + merge_gap:
            previous = merged[-1]
            merged[-1] = SpeechRange(previous.start_sample, max(previous.end_sample, item.end_sample))
        else:
            merged.append(item)

    bounded: list[SpeechRange] = []
    for item in merged:
        start = item.start_sample
        while start < item.end_sample:
            end = min(item.end_sample, start + max_samples)
            bounded.append(SpeechRange(start, end))
            start = end
    return bounded


def energy_speech_ranges(
    samples: list[float],
    *,
    sample_rate: int,
    frame_seconds: float = 0.03,
    absolute_floor: float = 0.008,
) -> list[SpeechRange]:
    """Cheap model-independent fallback that finds clearly non-silent frames."""
    if not samples or sample_rate <= 0:
        return []
    frame_size = max(1, round(frame_seconds * sample_rate))
    energies: list[float] = []
    for offset in range(0, len(samples), frame_size):
        frame = samples[offset : offset + frame_size]
        energies.append(math.sqrt(sum(value * value for value in frame) / len(frame)))
    sorted_energies = sorted(energies)
    noise_floor = sorted_energies[max(0, len(sorted_energies) // 5 - 1)]
    threshold = max(absolute_floor, noise_floor * 3.0)

    ranges: list[SpeechRange] = []
    start: int | None = None
    for index, energy in enumerate(energies):
        if energy >= threshold and start is None:
            start = index * frame_size
        elif energy < threshold and start is not None:
            ranges.append(SpeechRange(start, min(len(samples), index * frame_size)))
            start = None
    if start is not None:
        ranges.append(SpeechRange(start, len(samples)))
    return ranges


def fixed_window_ranges(
    total_samples: int, *, sample_rate: int, max_segment_seconds: float
) -> list[SpeechRange]:
    """Lossless final fallback: cover the entire recording in bounded windows."""
    if total_samples <= 0 or sample_rate <= 0:
        return []
    window = max(1, round(sample_rate * max_segment_seconds))
    return [
        SpeechRange(start, min(total_samples, start + window))
        for start in range(0, total_samples, window)
    ]
