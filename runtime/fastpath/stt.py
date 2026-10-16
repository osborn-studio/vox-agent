"""STT adapter — unified interface for all speech-to-text providers.

Fast path only: get text back as fast as possible.
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
import time
from pathlib import Path
from typing import Callable, Protocol

import httpx
import structlog

from runtime.models.config import VoxLogConfig

logger = structlog.get_logger()


class STTError(Exception):
    pass


class STTResult:
    def __init__(self, text: str, provider: str, model: str, latency_ms: int):
        self.text = text
        self.provider = provider
        self.model = model
        self.latency_ms = latency_ms


class STTProvider(Protocol):
    async def transcribe(self, audio: bytes) -> str: ...


def _require_transcript(value: object, provider: str) -> str:
    if isinstance(value, str) and (text := value.strip()):
        return text
    raise STTError(f"{provider} returned no speech.")


def _is_local_route(name: str) -> bool:
    normalized = name.strip().lower()
    return normalized in {
        "local",
        "local-tiny",
        "local-small",
        "whisper-cpp",
        "whisper.cpp",
        "whispercpp-local",
        "whispercpp-local-tiny",
        "whispercpp-local-small",
        "qwen-local",
        "qwen-0.6b",
        "sherpa",
        "sherpa-sensevoice",
        "sensevoice-local",
    }


def _route_timeout(name: str, *, fallback: bool = False) -> float:
    base = 60.0 if _is_local_route(name) else 30.0
    return base * (2.0 if fallback else 1.0)


def _write_windows_safe_temp_wav(audio: bytes) -> Path:
    if not audio or len(audio) > 64 * 1024 * 1024:
        raise STTError("Local ASR audio is empty or exceeds the 64 MiB limit.")
    temporary = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    path = Path(temporary.name)
    try:
        temporary.write(audio)
        temporary.flush()
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise STTError("Local ASR could not stage the audio payload.") from exc
    finally:
        temporary.close()
    return path


# --- Providers ---

class QwenSTT:
    REGIONS = {
        "us": ("https://dashscope-us.aliyuncs.com/api/v1", "qwen3-asr-flash-us"),
        "cn": ("https://dashscope.aliyuncs.com/api/v1", "qwen3-asr-flash"),
        "intl": ("https://dashscope-intl.aliyuncs.com/api/v1", "qwen3-asr-flash"),
    }

    def __init__(self, api_key: str, region: str = "us"):
        self.api_key = api_key
        self.base_url, self.model = self.REGIONS.get(region, self.REGIONS["us"])

    async def transcribe(self, audio: bytes) -> str:
        audio_b64 = base64.b64encode(audio).decode()
        from runtime.audio import detect_format
        fmt = detect_format(audio)
        mime = {"wav": "audio/wav", "ogg": "audio/ogg"}.get(fmt, "audio/wav")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/services/aigc/multimodal-generation/generation",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "input": {"messages": [{"content": [{"audio": f"data:{mime};base64,{audio_b64}"}], "role": "user"}]},
                    "parameters": {"asr_options": {"enable_itn": True}},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            output = data.get("output") if isinstance(data, dict) else None
            choices = output.get("choices") if isinstance(output, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices else None
            message = choice.get("message") if isinstance(choice, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                return _require_transcript(
                    "".join(
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict) and isinstance(item.get("text"), str)
                    ),
                    "Qwen ASR",
                )
            if isinstance(content, str):
                return _require_transcript(content, "Qwen ASR")
            raise STTError("Qwen ASR response did not contain transcript text.")


class OpenAIWhisperSTT:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.model = "whisper-1"

    async def transcribe(self, audio: bytes) -> str:
        from runtime.audio import detect_format
        fmt = detect_format(audio)
        ext = {"wav": "wav", "ogg": "ogg"}.get(fmt, "webm")
        mime = {"wav": "audio/wav", "ogg": "audio/ogg"}.get(fmt, "audio/webm")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (f"audio.{ext}", audio, mime)},
                data={"model": "whisper-1"},
            )
            resp.raise_for_status()
            data = resp.json()
            return _require_transcript(
                data.get("text") if isinstance(data, dict) else None,
                "OpenAI Whisper",
            )


class LocalWhisperCpp:
    """whisper.cpp via pywhispercpp with a Windows-safe temporary file.

    Tier 1: lowest latency, zero network, zero cost.
    Model loaded once, reused for all subsequent calls.
    """

    _model = None
    _model_name = "base"  # tiny(39MB) / base(142MB) / small(466MB) / medium(1.5GB)

    @classmethod
    def ensure_model(cls, model_name: str = "base"):
        if cls._model is None or cls._model_name != model_name:
            from pywhispercpp.model import Model
            cls._model = Model(model_name, print_progress=False)
            cls._model_name = model_name
            logger.info("whisper_cpp.loaded", model=model_name)

    def __init__(self, model_name: str = "base"):
        self.model_name = model_name

    async def transcribe(self, audio: bytes) -> str:
        try:
            LocalWhisperCpp.ensure_model(self.model_name)
        except Exception as exc:
            logger.warning("whisper_cpp.unavailable", model=self.model_name)
            raise STTError("Local whisper.cpp runtime is unavailable.") from exc

        path = _write_windows_safe_temp_wav(audio)
        try:
            segments = await asyncio.get_running_loop().run_in_executor(
                None, LocalWhisperCpp._model.transcribe, str(path)
            )
        except Exception as exc:
            logger.warning("whisper_cpp.transcribe_fail", model=self.model_name)
            raise STTError("Local whisper.cpp transcription failed.") from exc
        finally:
            path.unlink(missing_ok=True)
        text = " ".join(
            segment.text.strip()
            for segment in segments
            if isinstance(getattr(segment, "text", None), str) and segment.text.strip()
        )
        if not text:
            raise STTError("Local whisper.cpp returned no speech.")
        return text


class SherpaSenseVoiceSTT:
    """Real local SenseVoice adapter shared with the long-audio engine path."""

    model = "sherpa-onnx-sense-voice"

    def __init__(self, *, adapter_factory: Callable[[], object] | None = None) -> None:
        self._adapter_factory = adapter_factory or self._default_adapter

    @staticmethod
    def _default_adapter() -> object:
        from engine.asr_sherpa import SherpaOnnxAsrAdapter

        return SherpaOnnxAsrAdapter()

    async def transcribe(self, audio: bytes) -> str:
        path = _write_windows_safe_temp_wav(audio)
        try:
            adapter = self._adapter_factory()
            segments = await asyncio.get_running_loop().run_in_executor(
                None,
                adapter.transcribe,
                "fastpath-local",
                str(path),
                [],
            )
        except Exception as exc:
            logger.warning("sherpa_sensevoice.transcribe_fail")
            raise STTError("Local Sherpa SenseVoice transcription failed.") from exc
        finally:
            path.unlink(missing_ok=True)
        text = " ".join(
            value.strip()
            for segment in segments
            if isinstance(segment, dict)
            and isinstance((value := segment.get("textNormalized")), str)
            and value.strip()
        )
        if not text:
            raise STTError("Local Sherpa SenseVoice returned no speech.")
        return text


class SiliconFlowSTT:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.siliconflow.cn/v1"
        self.model = "FunAudioLLM/SenseVoiceSmall"

    async def transcribe(self, audio: bytes) -> str:
        from runtime.audio import detect_format
        fmt = detect_format(audio)
        ext = {"wav": "wav", "ogg": "ogg"}.get(fmt, "wav")
        mime = {"wav": "audio/wav"}.get(fmt, "audio/wav")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (f"audio.{ext}", audio, mime)},
                data={"model": self.model},
            )
            resp.raise_for_status()
            data = resp.json()
            return _require_transcript(
                data.get("text") if isinstance(data, dict) else None,
                "SiliconFlow ASR",
            )


# --- Router ---

def _get_provider(name: str, config: VoxLogConfig) -> STTProvider:
    key = config.get_stt_key(name)
    normalized = name.strip().lower()
    if normalized in {"qwen-local", "qwen-0.6b"}:
        return QwenLocalASR()
    if normalized in {"sherpa", "sherpa-sensevoice", "sensevoice-local"}:
        return SherpaSenseVoiceSTT()
    if normalized in {"local", "whisper-cpp", "whisper.cpp", "whispercpp-local"}:
        return LocalWhisperCpp(model_name="base")
    if normalized in {"local-tiny", "whispercpp-local-tiny"}:
        return LocalWhisperCpp(model_name="tiny")
    if normalized in {"local-small", "whispercpp-local-small"}:
        return LocalWhisperCpp(model_name="small")
    if not key:
        raise STTError(f"{normalized} credential is not configured.")
    if "qwen-cn" in normalized:
        return QwenSTT(key, region="cn")
    if "qwen-us" in normalized:
        return QwenSTT(key, region="us")
    if "qwen" in normalized:
        return QwenSTT(key, region="us")
    if "openai" in normalized:
        return OpenAIWhisperSTT(key)
    if "silicon" in normalized:
        return SiliconFlowSTT(key)
    raise STTError("Unknown STT provider.")


async def transcribe(audio: bytes, config: VoxLogConfig, override: str | None = None) -> STTResult:
    """Fast path STT with failover. Returns ASAP."""
    profile = config.profile
    main_name = override or profile.stt_main
    fallback_name = profile.stt_fallback
    # Try main
    start = time.monotonic()
    try:
        provider = _get_provider(main_name, config)
        text = await asyncio.wait_for(
            provider.transcribe(audio), timeout=_route_timeout(main_name)
        )
        latency = int((time.monotonic() - start) * 1000)
        logger.info("stt.ok", provider=main_name, ms=latency)
        model = getattr(provider, "model", getattr(provider, "model_name", main_name))
        return STTResult(text=text, provider=main_name, model=model, latency_ms=latency)
    except (asyncio.TimeoutError, httpx.HTTPError, STTError, ValueError) as exc:
        logger.warning("stt.main_fail", provider=main_name, error_type=type(exc).__name__)

    # Try fallback
    start = time.monotonic()
    try:
        provider = _get_provider(fallback_name, config)
        text = await asyncio.wait_for(
            provider.transcribe(audio), timeout=_route_timeout(fallback_name, fallback=True)
        )
        latency = int((time.monotonic() - start) * 1000)
        logger.info("stt.fallback_ok", provider=fallback_name, ms=latency)
        model = getattr(provider, "model", getattr(provider, "model_name", fallback_name))
        return STTResult(text=text, provider=fallback_name, model=model, latency_ms=latency)
    except (asyncio.TimeoutError, httpx.HTTPError, STTError, ValueError) as exc:
        logger.error("stt.both_fail", error_type=type(exc).__name__)
        raise STTError("Both configured STT routes failed.")


class QwenLocalASR:
    """Qwen3-ASR-0.6B running locally via qwen-asr package.
    
    Good for: high quality, multilingual, Chinese-English mixing
    Bad for: latency (15s+ first inference, ~3-5s warm)
    Best use: slow path high-quality re-transcription, or when no network
    """

    _model = None
    model = "Qwen/Qwen3-ASR-0.6B"

    @classmethod
    def ensure_model(cls):
        if cls._model is None:
            import torch
            from qwen_asr import Qwen3ASRModel
            mps = getattr(torch.backends, "mps", None)
            device = "mps" if mps is not None and mps.is_available() else "cpu"
            cls._model = Qwen3ASRModel.from_pretrained(
                "Qwen/Qwen3-ASR-0.6B",
                dtype=torch.float32,
                device_map=device,
            )
            logger.info("qwen_local.loaded", device=device)

    async def transcribe(self, audio: bytes) -> str:
        try:
            QwenLocalASR.ensure_model()
        except Exception as exc:
            raise STTError("Local Qwen3 ASR runtime is unavailable.") from exc
        path = _write_windows_safe_temp_wav(audio)
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None, QwenLocalASR._model.transcribe, [str(path)]
            )
        except Exception as exc:
            raise STTError("Local Qwen3 ASR transcription failed.") from exc
        finally:
            path.unlink(missing_ok=True)
        if result and isinstance(getattr(result[0], "text", None), str):
            text = result[0].text.strip()
            if text:
                return text
        raise STTError("Local Qwen3 ASR returned no speech.")
