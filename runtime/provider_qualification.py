"""Provider readiness inventory for the Vox Agent desktop control plane.

Qualification deliberately separates support, configuration, runtime discovery,
and live evidence. Static inventory never contacts an external provider and never
returns credential material.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Callable, Literal

from runtime.llm_provider import resolve_llm_provider
from runtime.local_asr_paths import sherpa_assets_available
from runtime.models.config import VoxLogConfig

CONTRACT_VERSION = "2026-07-19.2"

ProviderKind = Literal["stt", "llm"]
ProviderTransport = Literal["cloud", "local"]
ProviderSupport = Literal[
    "supported",
    "install_required",
    "deferred",
    "platform_unavailable",
]


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    kind: ProviderKind
    transport: ProviderTransport
    capabilities: tuple[str, ...]
    support: ProviderSupport
    credential: str | None = None
    runtime: str | None = None
    install_command: str | None = None
    asset_command: str | None = None


@dataclass(frozen=True)
class LocalRuntimeInspection:
    """Secret-free result of checking one local provider installation."""

    package_available: bool
    assets_available: bool | None

    @property
    def ready(self) -> bool:
        return self.package_available and self.assets_available is not False


PROVIDERS = (
    ProviderSpec(
        "qwen-us",
        "Qwen ASR US",
        "stt",
        "cloud",
        ("batch", "multilingual"),
        "supported",
        credential="dashscope_us",
    ),
    ProviderSpec(
        "qwen-cn",
        "Qwen ASR CN",
        "stt",
        "cloud",
        ("batch", "multilingual"),
        "supported",
        credential="dashscope_cn",
    ),
    ProviderSpec(
        "openai-whisper",
        "OpenAI Whisper",
        "stt",
        "cloud",
        ("batch", "multilingual"),
        "supported",
        credential="openai",
    ),
    ProviderSpec(
        "siliconflow-whisper",
        "SiliconFlow SenseVoice",
        "stt",
        "cloud",
        ("batch", "multilingual"),
        "supported",
        credential="siliconflow",
    ),
    ProviderSpec(
        "whispercpp-local",
        "whisper.cpp Local",
        "stt",
        "local",
        ("batch", "offline"),
        "install_required",
        runtime="whispercpp-local",
        install_command='py -m pip install ".[local]"',
        asset_command=(
            "py scripts/verify_windows_asr.py --provider whispercpp-local --audio SAMPLE.wav"
        ),
    ),
    ProviderSpec(
        "qwen-local",
        "Qwen3 ASR Local",
        "stt",
        "local",
        ("batch", "offline", "multilingual"),
        "install_required",
        runtime="qwen-local",
        install_command="py -3.12 -m pip install -U qwen-asr",
        asset_command=(
            "py scripts/verify_windows_asr.py --provider qwen-local --audio SAMPLE.wav"
        ),
    ),
    ProviderSpec(
        "sherpa-sensevoice",
        "Sherpa SenseVoice Local",
        "stt",
        "local",
        ("batch", "offline", "long_audio"),
        "install_required",
        runtime="sherpa-sensevoice",
        install_command='py -m pip install ".[asr]"',
        asset_command="py scripts/fetch_sherpa_model.py",
    ),
    ProviderSpec(
        "additional-cloud-asr",
        "Additional cloud ASR family",
        "stt",
        "cloud",
        ("batch", "streaming"),
        "deferred",
    ),
    ProviderSpec(
        "openai-gpt",
        "OpenAI GPT",
        "llm",
        "cloud",
        ("polish", "chat"),
        "supported",
        credential="openai",
    ),
    ProviderSpec(
        "qwen-turbo",
        "Qwen Bailian",
        "llm",
        "cloud",
        ("polish", "chat"),
        "supported",
        credential="dashscope_any",
    ),
    ProviderSpec(
        "doubao",
        "Doubao ARK",
        "llm",
        "cloud",
        ("polish", "chat"),
        "supported",
        credential="doubao",
    ),
    ProviderSpec(
        "claude",
        "Claude",
        "llm",
        "cloud",
        ("polish", "chat"),
        "supported",
        credential="anthropic",
    ),
    ProviderSpec(
        "openai-compatible-custom",
        "Custom OpenAI-compatible",
        "llm",
        "cloud",
        ("polish", "chat"),
        "supported",
        credential="custom_llm",
    ),
    ProviderSpec(
        "ollama",
        "Ollama Local",
        "llm",
        "local",
        ("polish", "chat", "offline"),
        "install_required",
        runtime="ollama",
        install_command="winget install Ollama.Ollama",
        asset_command="ollama pull qwen2.5:7b",
    ),
)


def _platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unknown"


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _sherpa_assets_available() -> bool:
    return sherpa_assets_available()


def inspect_local_runtime(provider_id: str) -> LocalRuntimeInspection:
    if provider_id == "whispercpp-local":
        return LocalRuntimeInspection(_module_available("pywhispercpp"), None)
    if provider_id == "qwen-local":
        return LocalRuntimeInspection(
            _module_available("qwen_asr") and _module_available("torch"), None
        )
    if provider_id == "sherpa-sensevoice":
        return LocalRuntimeInspection(
            _module_available("sherpa_onnx"), _sherpa_assets_available()
        )
    if provider_id == "ollama":
        return LocalRuntimeInspection(shutil.which("ollama") is not None, None)
    return LocalRuntimeInspection(False, None)


def _proxy_environment() -> dict[str, bool]:
    """Report only proxy presence; values can contain credentials and stay private."""

    names = {name.upper() for name in os.environ}
    return {
        "httpx_trust_env": True,
        "proxy_environment_configured": bool(
            names.intersection({"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"})
        ),
        "no_proxy_configured": "NO_PROXY" in names,
    }


def _credential_configured(config: VoxLogConfig, credential: str | None) -> bool:
    values = {
        "dashscope_us": bool(config.dashscope_key_us),
        "dashscope_cn": bool(config.dashscope_key_cn or config.dashscope_key_us),
        "dashscope_any": bool(config.dashscope_key_cn or config.dashscope_key_us),
        "openai": bool(config.openai_key),
        "siliconflow": bool(config.siliconflow_key),
        "doubao": bool(config.doubao_key),
        "anthropic": bool(config.anthropic_key),
        "custom_llm": bool(config.custom_llm_base_url and config.custom_llm_model),
    }
    return values.get(credential or "", False)


def _profile_provider_id(route: str, kind: ProviderKind) -> str | None:
    normalized = route.strip().lower()
    if kind == "stt":
        if "qwen-cn" in normalized:
            return "qwen-cn"
        if "qwen-us" in normalized or normalized == "qwen":
            return "qwen-us"
        if "openai" in normalized:
            return "openai-whisper"
        if "silicon" in normalized:
            return "siliconflow-whisper"
        if normalized in {"qwen-local", "qwen-0.6b"}:
            return "qwen-local"
        if "sherpa" in normalized or "sensevoice" in normalized:
            return "sherpa-sensevoice"
        if "local" in normalized or "whisper" in normalized:
            return "whispercpp-local"
        return None
    if "custom" in normalized or "openai-compatible" in normalized:
        return "openai-compatible-custom"
    if "openai" in normalized:
        return "openai-gpt"
    if "qwen" in normalized or "bailian" in normalized:
        return "qwen-turbo"
    if "doubao" in normalized or "ark" in normalized:
        return "doubao"
    if "claude" in normalized or "anthropic" in normalized:
        return "claude"
    if "ollama" in normalized:
        return "ollama"
    return None


def _active_roles(config: VoxLogConfig) -> dict[str, list[str]]:
    profile = config.profile
    routes = (
        ("stt_main", profile.stt_main, "stt"),
        ("stt_fallback", profile.stt_fallback, "stt"),
        ("llm_main", profile.llm_main, "llm"),
        ("llm_fallback", profile.llm_fallback, "llm"),
    )
    roles: dict[str, list[str]] = {}
    for role, route, kind in routes:
        provider_id = _profile_provider_id(route, kind)
        if provider_id:
            roles.setdefault(provider_id, []).append(role)
    return roles


def _action_for(
    spec: ProviderSpec,
    readiness: str,
) -> str:
    if readiness == "ready":
        if spec.kind == "stt":
            return "Runtime and declared assets are available; run a real audio smoke before release."
        return "Local runtime is available; run a real prompt smoke before release."
    if readiness == "configured_unverified":
        if spec.id == "openai-compatible-custom":
            return "Endpoint and model are stored; run an explicit live probe before qualification."
        return "Credential is stored; run an explicit live probe before release qualification."
    if readiness == "missing_credential":
        if spec.id == "openai-compatible-custom":
            return "Configure a safe HTTP(S) base URL and model; store a key only if required."
        if spec.kind == "llm":
            return "Configure a valid provider credential and model in the secure settings workspace."
        return "Store the provider credential in the system keyring."
    if readiness == "missing_runtime":
        if spec.id == "sherpa-sensevoice":
            return (
                "Install the Sherpa runtime and SenseVoice and VAD model assets with the "
                "displayed commands."
            )
        return f"Install the {spec.label} runtime and complete its explicit model smoke."
    if readiness == "platform_unavailable":
        return f"{spec.label} is not available on this platform."
    return "Provider adapter is tracked for a later Windows implementation slice."


def build_provider_qualification(
    config: VoxLogConfig,
    *,
    platform: str | None = None,
    runtime_inspector: Callable[[str], LocalRuntimeInspection] = inspect_local_runtime,
) -> dict:
    """Build a secret-free, side-effect-free provider qualification snapshot."""

    resolved_platform = platform or _platform_name()
    roles = _active_roles(config)
    providers: list[dict] = []

    for spec in PROVIDERS:
        runtime_available: bool | None = None
        package_available: bool | None = None
        assets_available: bool | None = None
        configured = False
        probe_status = "not_run"

        if spec.support == "platform_unavailable":
            readiness = "platform_unavailable"
        elif spec.support == "deferred":
            readiness = "not_implemented"
        elif spec.transport == "local":
            inspection = runtime_inspector(spec.runtime or spec.id)
            package_available = inspection.package_available
            assets_available = inspection.assets_available
            runtime_available = inspection.ready
            configured = runtime_available
            readiness = "ready" if runtime_available else "missing_runtime"
        elif spec.kind == "llm":
            configured = resolve_llm_provider(config, spec.id) is not None
            readiness = "configured_unverified" if configured else "missing_credential"
        else:
            configured = _credential_configured(config, spec.credential)
            readiness = "configured_unverified" if configured else "missing_credential"

        providers.append(
            {
                "id": spec.id,
                "label": spec.label,
                "kind": spec.kind,
                "transport": spec.transport,
                "capabilities": list(spec.capabilities),
                "support": spec.support,
                "readiness": readiness,
                "configured": configured,
                "runtime_available": runtime_available,
                "package_available": package_available,
                "assets_available": assets_available,
                "probe_status": probe_status,
                "active_roles": roles.get(spec.id, []),
                "action": _action_for(spec, readiness),
                "install_command": spec.install_command,
                "asset_command": spec.asset_command,
            }
        )

    active = [provider for provider in providers if provider["active_roles"]]
    active_configured = bool(active) and all(provider["configured"] for provider in active)
    qualified_count = sum(1 for provider in providers if provider["probe_status"] == "passed")
    configured_count = sum(1 for provider in providers if provider["configured"])
    action_required_count = sum(
        1
        for provider in providers
        if provider["active_roles"] and provider["probe_status"] != "passed"
    )

    return {
        "contract_version": CONTRACT_VERSION,
        "platform": resolved_platform,
        "active_profile": config.active_profile,
        "network_environment": _proxy_environment(),
        "summary": {
            "qualified_count": qualified_count,
            "configured_count": configured_count,
            "action_required_count": action_required_count,
            "active_routes_configured": active_configured,
        },
        "providers": providers,
    }
