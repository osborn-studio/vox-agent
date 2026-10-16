"""Unit tests for long-recording voice activity segmentation."""

from __future__ import annotations

import sys
import types

import pytest

from engine.vad import SherpaVadSegmenter, SpeechRange, normalize_speech_ranges


class _FakeSileroConfig:
    model = ""
    threshold = 0.0
    min_silence_duration = 0.0
    min_speech_duration = 0.0
    max_speech_duration = 0.0
    window_size = 0


class _FakeVadConfig:
    def __init__(self) -> None:
        self.silero_vad = _FakeSileroConfig()
        self.sample_rate = 0
        self.num_threads = 0
        self.provider = ""
        self.debug = True

    def validate(self) -> bool:
        return True


class _FakeVad:
    instances: list["_FakeVad"] = []

    def __init__(self, config, buffer_size_in_seconds: float) -> None:
        self.config = config
        self.buffer_size_in_seconds = buffer_size_in_seconds
        self.accepted: list[list[float]] = []
        self.flushed = False
        self._segments = [
            types.SimpleNamespace(start=1600, samples=[0.2] * 3200),
            types.SimpleNamespace(start=9600, samples=[0.2] * 1600),
        ]
        self.instances.append(self)

    def accept_waveform(self, samples) -> None:
        self.accepted.append(list(samples))

    def flush(self) -> None:
        self.flushed = True

    @property
    def empty(self) -> bool:
        return not self._segments

    @property
    def front(self):
        return self._segments[0]

    def pop(self) -> None:
        self._segments.pop(0)


@pytest.fixture
def fake_sherpa_vad(monkeypatch):
    fake = types.ModuleType("sherpa_onnx")
    fake.VadModelConfig = _FakeVadConfig
    fake.VoiceActivityDetector = _FakeVad
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    _FakeVad.instances.clear()
    return fake


def test_sherpa_vad_uses_official_streaming_api(fake_sherpa_vad, tmp_path):
    model = tmp_path / "silero_vad.onnx"
    model.touch()
    segmenter = SherpaVadSegmenter(model_path=model, threshold=0.35)

    ranges = segmenter.segment([0.1] * 12000, sample_rate=16000)

    assert ranges == [SpeechRange(1600, 4800), SpeechRange(9600, 11200)]
    detector = _FakeVad.instances[0]
    assert detector.config.silero_vad.model == str(model)
    assert detector.config.silero_vad.threshold == 0.35
    assert detector.config.silero_vad.window_size == 512
    assert detector.config.sample_rate == 16000
    assert detector.flushed is True
    assert len(detector.accepted) == 24
    assert len(detector.accepted[-1]) == 224


def test_sherpa_vad_requires_16khz_and_a_model(tmp_path):
    missing = tmp_path / "missing.onnx"
    segmenter = SherpaVadSegmenter(model_path=missing)
    with pytest.raises(RuntimeError, match="VAD model not found"):
        segmenter.segment([0.1], sample_rate=16000)

    missing.touch()
    with pytest.raises(ValueError, match="16 kHz"):
        segmenter.segment([0.1], sample_rate=8000)


def test_ranges_are_padded_merged_clipped_and_split():
    ranges = normalize_speech_ranges(
        [SpeechRange(100, 500), SpeechRange(550, 900), SpeechRange(2500, 3900)],
        total_samples=4000,
        sample_rate=1000,
        padding_seconds=0.1,
        merge_gap_seconds=0.1,
        max_segment_seconds=1.0,
    )

    assert ranges == [
        SpeechRange(0, 1000),
        SpeechRange(2400, 3400),
        SpeechRange(3400, 4000),
    ]
