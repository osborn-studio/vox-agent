"""Tests for engine.asr — ASR adapter abstraction."""

import pytest

from engine.asr import MockAsrAdapter, get_asr_adapter


class TestMockAsrAdapter:
    def test_name(self):
        assert MockAsrAdapter().name == "mock"

    def test_transcribe_returns_deterministic_segments(self):
        adapter = MockAsrAdapter()
        first = adapter.transcribe(session_id="cap_x", audio_path=None, language_hints=["zh", "ja", "en"])
        second = adapter.transcribe(session_id="cap_x", audio_path=None, language_hints=["zh", "ja", "en"])
        assert first == second
        assert len(first) >= 6

    def test_segments_are_camelcase_payloads(self):
        segments = MockAsrAdapter().transcribe(session_id="cap_x", audio_path=None, language_hints=[])
        for seg in segments:
            for key in ("speakerId", "startMs", "endMs", "textRaw", "textNormalized", "model", "language"):
                assert key in seg, f"missing {key}"
            assert "id" not in seg  # repository assigns ids
            assert "sessionId" not in seg  # endpoint supplies the session
        assert segments[0]["model"] == "mock-asr"

    def test_timestamps_increase(self):
        segments = MockAsrAdapter().transcribe(session_id="cap_x", audio_path=None, language_hints=[])
        for prev, cur in zip(segments, segments[1:]):
            assert cur["startMs"] >= prev["endMs"]

    def test_covers_three_languages(self):
        segments = MockAsrAdapter().transcribe(session_id="cap_x", audio_path=None, language_hints=[])
        assert {"zh", "ja", "en"} <= {s["language"] for s in segments}


class TestRegistry:
    def test_default_is_mock(self, monkeypatch):
        monkeypatch.delenv("VOXAGENT_ASR_ADAPTER", raising=False)
        assert get_asr_adapter().name == "mock"

    def test_env_selection(self, monkeypatch):
        monkeypatch.setenv("VOXAGENT_ASR_ADAPTER", "mock")
        assert get_asr_adapter().name == "mock"

    def test_unknown_adapter_raises(self):
        with pytest.raises(ValueError, match="unknown ASR adapter"):
            get_asr_adapter("does-not-exist")


class TestSherpaRegistration:
    def test_sherpa_is_registered(self):
        from engine.asr import _ADAPTER_FACTORIES
        assert "sherpa" in _ADAPTER_FACTORIES

    def test_importing_asr_does_not_import_sherpa_module(self):
        import engine
        import importlib
        import sys
        saved_package_attribute = engine.asr
        saved = {k: v for k, v in sys.modules.items() if k.startswith("engine.asr")}
        for k in saved:
            del sys.modules[k]
        try:
            importlib.import_module("engine.asr")
            assert "engine.asr_sherpa" not in sys.modules
        finally:
            for key in tuple(sys.modules):
                if key.startswith("engine.asr"):
                    del sys.modules[key]
            sys.modules.update(saved)
            engine.asr = saved_package_attribute

    def test_get_sherpa_adapter_returns_named_adapter(self):
        from engine.asr import get_asr_adapter
        adapter = get_asr_adapter("sherpa")
        assert adapter.name == "sherpa"
