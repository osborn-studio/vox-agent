"""Tests for engine.asr_sherpa — real ASR adapter.

Unit tests fake the sherpa_onnx module so they run without the package/model.
The integration test at the bottom runs only when a real model dir exists.

API-COMPAT: sherpa-onnx 1.13.3 (the version installed in .venv) exposes the
decoded transcript via ``OfflineStream.result`` (a plain attribute/property
populated in-place by ``OfflineRecognizer.decode_stream``) — there is no
``get_result`` / ``get_result_all`` method on ``OfflineRecognizer``. The fake
recognizer below mirrors that: ``decode_stream`` sets ``stream.result``, and
the adapter reads ``stream.result`` directly after decoding.
"""

import os
import sys
import types
import wave
from pathlib import Path

import pytest

from engine.asr_sherpa import SherpaOnnxAsrAdapter, DEFAULT_MODEL_DIR
from engine.vad import SpeechRange


def _write_wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


class _FakeStream:
    def __init__(self):
        self.result = None

    def accept_waveform(self, sample_rate, samples):
        self.accepted = (sample_rate, len(samples))


class _FakeRecognizer:
    @classmethod
    def from_sense_voice(cls, **kwargs):
        inst = cls()
        inst.kwargs = kwargs
        return inst

    def create_stream(self):
        stream = _FakeStream()
        self.streams = getattr(self, "streams", []) + [stream]
        return stream

    def decode_stream(self, stream):
        stream.result = types.SimpleNamespace(text="你好 world", lang="<|zh|>", timestamps=[])


@pytest.fixture
def fake_sherpa(monkeypatch, tmp_path):
    fake = types.ModuleType("sherpa_onnx")
    fake.OfflineRecognizer = _FakeRecognizer
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.int8.onnx").touch()
    (model_dir / "tokens.txt").touch()
    monkeypatch.setenv("VOXAGENT_SHERPA_MODEL_DIR", str(model_dir))
    return fake


class TestSherpaAdapter:
    def test_name(self):
        assert SherpaOnnxAsrAdapter().name == "sherpa"

    def test_missing_model_dir_raises_helpful_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VOXAGENT_SHERPA_MODEL_DIR", str(tmp_path / "nope"))
        adapter = SherpaOnnxAsrAdapter()
        with pytest.raises(RuntimeError, match="fetch_sherpa_model"):
            adapter.transcribe(session_id="cap_x", audio_path="/tmp/a.wav", language_hints=[])

    def test_missing_audio_path_raises(self, fake_sherpa):
        adapter = SherpaOnnxAsrAdapter()
        with pytest.raises(ValueError, match="audioPath"):
            adapter.transcribe(session_id="cap_x", audio_path=None, language_hints=[])

    def test_missing_audio_file_raises(self, fake_sherpa, tmp_path):
        adapter = SherpaOnnxAsrAdapter()
        with pytest.raises(RuntimeError, match="audio file not found"):
            adapter.transcribe(session_id="cap_x", audio_path=str(tmp_path / "missing.wav"), language_hints=[])

    def test_transcribe_returns_contract_payload(self, fake_sherpa, tmp_path):
        wav = tmp_path / "a.wav"
        _write_wav(wav, seconds=2.0)
        adapter = SherpaOnnxAsrAdapter()
        segments = adapter.transcribe(session_id="cap_x", audio_path=str(wav), language_hints=["zh"])
        assert len(segments) == 1
        seg = segments[0]
        assert seg["textRaw"] == "你好 world"
        assert seg["textNormalized"] == "你好 world"
        assert seg["language"] == "zh"
        assert seg["model"].startswith("sherpa-onnx")
        assert seg["startMs"] == 0
        assert seg["endMs"] == 2000
        assert seg["speakerId"] == "speaker_1"
        assert 0 <= seg["confidence"] <= 1
        assert "id" not in seg and "sessionId" not in seg

    def test_model_loaded_once(self, fake_sherpa, tmp_path):
        wav = tmp_path / "a.wav"
        _write_wav(wav)
        adapter = SherpaOnnxAsrAdapter()
        adapter.transcribe(session_id="cap_x", audio_path=str(wav), language_hints=[])
        first = adapter._recognizer
        adapter.transcribe(session_id="cap_x", audio_path=str(wav), language_hints=[])
        assert adapter._recognizer is first

    def test_long_recording_is_vad_segmented_with_source_timestamps(
        self, fake_sherpa, tmp_path
    ):
        class FakeVad:
            def segment(self, samples, sample_rate):
                assert sample_rate == 16000
                return [SpeechRange(16000, 32000), SpeechRange(48000, 64000)]

        wav = tmp_path / "meeting.wav"
        _write_wav(wav, seconds=5.0, rate=8000)
        adapter = SherpaOnnxAsrAdapter(
            vad_segmenter=FakeVad(),
            long_audio_seconds=1.0,
            padding_seconds=0.0,
        )

        segments = adapter.transcribe("cap_long", str(wav), ["zh"])

        assert [(item["startMs"], item["endMs"]) for item in segments] == [
            (1000, 2000),
            (3000, 4000),
        ]
        assert [stream.accepted for stream in adapter._recognizer.streams] == [
            (16000, 16000),
            (16000, 16000),
        ]

    def test_vad_false_negative_falls_back_without_dropping_audio(
        self, fake_sherpa, tmp_path
    ):
        class EmptyVad:
            def segment(self, samples, sample_rate):
                return []

        wav = tmp_path / "quiet-meeting.wav"
        _write_wav(wav, seconds=2.0)
        adapter = SherpaOnnxAsrAdapter(
            vad_segmenter=EmptyVad(),
            long_audio_seconds=0.5,
            max_segment_seconds=1.0,
        )

        segments = adapter.transcribe("cap_quiet", str(wav), [])

        assert len(segments) == 2
        assert segments[0]["startMs"] == 0
        assert segments[-1]["endMs"] == 2000


_REAL_MODEL = Path(os.environ.get("VOXAGENT_SHERPA_MODEL_DIR", str(DEFAULT_MODEL_DIR)))
_REAL_AUDIO = os.environ.get("VOXAGENT_SHERPA_TEST_WAV", "")


@pytest.mark.skipif(
    not (_REAL_MODEL.exists() and _REAL_AUDIO and Path(_REAL_AUDIO).exists()),
    reason="real sherpa model or test wav not available",
)
class TestSherpaRealModel:
    def test_real_transcription_produces_text(self):
        adapter = SherpaOnnxAsrAdapter()
        segments = adapter.transcribe(session_id="cap_real", audio_path=_REAL_AUDIO, language_hints=[])
        assert segments and segments[0]["textRaw"].strip()
