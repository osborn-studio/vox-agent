from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.fastpath import stt
from runtime.fastpath.stt import LocalWhisperCpp, STTError, SherpaSenseVoiceSTT
from runtime.models.config import VoxLogConfig


@pytest.mark.asyncio
async def test_local_whisper_never_fabricates_a_transcript_when_runtime_is_missing(
    monkeypatch,
) -> None:
    def unavailable(_cls, _model_name: str = "base") -> None:
        raise ImportError("runtime missing")

    monkeypatch.setattr(LocalWhisperCpp, "ensure_model", classmethod(unavailable))

    with pytest.raises(STTError, match="runtime is unavailable"):
        await LocalWhisperCpp().transcribe(b"RIFF-not-a-real-wave")


@pytest.mark.asyncio
async def test_local_whisper_rejects_an_empty_runtime_result(monkeypatch) -> None:
    class EmptyModel:
        @staticmethod
        def transcribe(_path: str) -> list:
            return []

    def available(cls, _model_name: str = "base") -> None:
        cls._model = EmptyModel()

    monkeypatch.setattr(LocalWhisperCpp, "ensure_model", classmethod(available))

    with pytest.raises(STTError, match="returned no speech"):
        await LocalWhisperCpp().transcribe(b"RIFF-empty")


@pytest.mark.asyncio
async def test_sherpa_fastpath_uses_real_adapter_output_without_placeholder_text() -> None:
    class Adapter:
        @staticmethod
        def transcribe(
            _session_id: str,
            audio_path: str | None,
            _language_hints: list[str],
        ) -> list[dict]:
            assert audio_path
            with open(audio_path, "rb") as audio_file:
                assert audio_file.read() == b"RIFF-real-audio"
            return [
                {"textNormalized": "真实"},
                {"textNormalized": " transcription"},
            ]

    provider = SherpaSenseVoiceSTT(adapter_factory=Adapter)

    assert await provider.transcribe(b"RIFF-real-audio") == "真实 transcription"


@pytest.mark.asyncio
async def test_sherpa_fastpath_rejects_empty_adapter_output() -> None:
    class Adapter:
        @staticmethod
        def transcribe(
            _session_id: str,
            _audio_path: str | None,
            _language_hints: list[str],
        ) -> list[dict]:
            return [
                {"textNormalized": "  "},
                {"textNormalized": None},
                SimpleNamespace(text="not a contract payload"),
            ]

    provider = SherpaSenseVoiceSTT(adapter_factory=Adapter)

    with pytest.raises(STTError, match="returned no speech"):
        await provider.transcribe(b"RIFF-silence")


@pytest.mark.asyncio
async def test_sherpa_main_route_is_not_rewritten_when_it_has_no_api_key(monkeypatch) -> None:
    config = VoxLogConfig()
    config.profiles["home"].stt_main = "sherpa-sensevoice"
    config.profiles["home"].stt_fallback = "openai-whisper"
    requested: list[str] = []

    class Provider:
        model = "sensevoice"

        @staticmethod
        async def transcribe(_audio: bytes) -> str:
            return "real local result"

    def get_provider(name: str, _config: VoxLogConfig) -> Provider:
        requested.append(name)
        return Provider()

    monkeypatch.setattr(stt, "_get_provider", get_provider)

    result = await stt.transcribe(b"RIFF-real-audio", config)

    assert requested == ["sherpa-sensevoice"]
    assert result.text == "real local result"


@pytest.mark.asyncio
async def test_selected_fallback_route_is_not_silently_replaced(monkeypatch) -> None:
    config = VoxLogConfig()
    config.profiles["home"].stt_main = "qwen-us"
    config.profiles["home"].stt_fallback = "sherpa-sensevoice"
    requested: list[str] = []

    class Provider:
        def __init__(self, name: str) -> None:
            self.name = name
            self.model = name

        async def transcribe(self, _audio: bytes) -> str:
            if self.name == "qwen-us":
                raise STTError("credential is not configured")
            return "selected fallback result"

    def get_provider(name: str, _config: VoxLogConfig) -> Provider:
        requested.append(name)
        return Provider(name)

    monkeypatch.setattr(stt, "_get_provider", get_provider)

    result = await stt.transcribe(b"RIFF-real-audio", config)

    assert requested == ["qwen-us", "sherpa-sensevoice"]
    assert result.provider == "sherpa-sensevoice"


@pytest.mark.asyncio
async def test_both_route_failure_is_bounded_and_does_not_echo_provider_details(
    monkeypatch,
) -> None:
    config = VoxLogConfig()

    def get_provider(_name: str, _config: VoxLogConfig):
        raise STTError("credential-like provider detail")

    monkeypatch.setattr(stt, "_get_provider", get_provider)

    with pytest.raises(STTError) as failure:
        await stt.transcribe(b"RIFF-real-audio", config)

    assert str(failure.value) == "Both configured STT routes failed."
    assert "credential-like" not in str(failure.value)
