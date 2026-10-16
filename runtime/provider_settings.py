"""Atomic validation for the desktop provider settings bridge."""

from __future__ import annotations

from typing import Any

from runtime.llm_provider import normalize_llm_base_url, normalize_llm_model
from runtime.models.config import VoxLogConfig

LLM_ROUTES = frozenset(
    {
        "openai-gpt",
        "qwen-turbo",
        "doubao",
        "claude",
        "openai-compatible-custom",
        "ollama",
    }
)
STT_ROUTES = frozenset(
    {
        "qwen-us",
        "qwen-cn",
        "openai-whisper",
        "siliconflow-whisper",
        "whispercpp-local",
        "qwen-local",
        "sherpa-sensevoice",
    }
)
DIGEST_PROVIDERS = LLM_ROUTES | {"auto", "openai", "qwen", "custom"}

_SECRET_FIELDS = (
    "dashscope_key_us",
    "dashscope_key_cn",
    "openai_key",
    "siliconflow_key",
    "doubao_key",
    "anthropic_key",
    "custom_llm_key",
)
_MODEL_FIELDS = (
    "openai_llm_model",
    "qwen_llm_model",
    "doubao_model",
    "anthropic_model",
    "ollama_model",
)
_ALLOWED_FIELDS = frozenset(_SECRET_FIELDS) | frozenset(_MODEL_FIELDS) | {
    "custom_llm_model",
    "custom_llm_base_url",
    "active_profile",
    "llm_main",
    "llm_fallback",
    "stt_main",
    "stt_fallback",
    "digest_enhancement_enabled",
    "digest_enhancement_provider",
}


def apply_provider_settings_update(config: VoxLogConfig, body: object) -> None:
    """Validate the whole patch before applying any mutation."""

    if not isinstance(body, dict):
        raise ValueError("provider settings payload must be an object")
    unknown_fields = set(body) - _ALLOWED_FIELDS
    if unknown_fields:
        raise ValueError("provider settings payload contains unknown fields")

    updates: dict[str, Any] = {}
    for field_name in _SECRET_FIELDS:
        if field_name not in body or body[field_name] is None:
            continue
        value = body[field_name]
        if (
            not isinstance(value, str)
            or len(value) > 20_000
            or any(char in value for char in "\r\n\0")
        ):
            raise ValueError(f"{field_name} is invalid")
        updates[field_name] = value.strip()

    for field_name in _MODEL_FIELDS:
        if field_name not in body or body[field_name] is None:
            continue
        value = body[field_name]
        if not isinstance(value, str):
            raise ValueError(f"{field_name} is invalid")
        normalized = normalize_llm_model(value)
        if normalized is None:
            raise ValueError(f"{field_name} is invalid")
        updates[field_name] = normalized

    if "custom_llm_model" in body and body["custom_llm_model"] is not None:
        value = body["custom_llm_model"]
        if not isinstance(value, str):
            raise ValueError("custom_llm_model is invalid")
        normalized = normalize_llm_model(value, allow_empty=True)
        if normalized is None:
            raise ValueError("custom_llm_model is invalid")
        updates["custom_llm_model"] = normalized

    if "custom_llm_base_url" in body and body["custom_llm_base_url"] is not None:
        value = body["custom_llm_base_url"]
        if not isinstance(value, str):
            raise ValueError("custom_llm_base_url is invalid")
        normalized = normalize_llm_base_url(value, allow_empty=True)
        if normalized is None:
            raise ValueError("custom_llm_base_url is invalid")
        updates["custom_llm_base_url"] = normalized

    profile_name = config.active_profile
    if "active_profile" in body and body["active_profile"] is not None:
        value = body["active_profile"]
        if not isinstance(value, str) or value.strip() not in config.profiles:
            raise ValueError("active_profile is invalid")
        profile_name = value.strip()
        updates["active_profile"] = profile_name

    for field_name in ("llm_main", "llm_fallback"):
        if field_name not in body or body[field_name] is None:
            continue
        value = body[field_name]
        if not isinstance(value, str) or value.strip() not in LLM_ROUTES:
            raise ValueError(f"{field_name} is invalid")
        updates[field_name] = value.strip()

    for field_name in ("stt_main", "stt_fallback"):
        if field_name not in body or body[field_name] is None:
            continue
        value = body[field_name]
        if not isinstance(value, str) or value.strip() not in STT_ROUTES:
            raise ValueError(f"{field_name} is invalid")
        updates[field_name] = value.strip()

    if "digest_enhancement_enabled" in body and body["digest_enhancement_enabled"] is not None:
        value = body["digest_enhancement_enabled"]
        if not isinstance(value, bool):
            raise ValueError("digest_enhancement_enabled must be a boolean")
        updates["digest_enhancement_enabled"] = value

    if "digest_enhancement_provider" in body and body["digest_enhancement_provider"] is not None:
        value = body["digest_enhancement_provider"]
        if not isinstance(value, str) or value.strip().lower() not in DIGEST_PROVIDERS:
            raise ValueError("digest_enhancement_provider is invalid")
        updates["digest_enhancement_provider"] = value.strip().lower()

    resulting_custom_url = updates.get("custom_llm_base_url", config.custom_llm_base_url)
    resulting_custom_model = updates.get("custom_llm_model", config.custom_llm_model)
    if bool(resulting_custom_url) != bool(resulting_custom_model):
        raise ValueError("custom LLM base URL and model must be configured together")

    resulting_main = updates.get("llm_main", config.profiles[profile_name].llm_main)
    resulting_fallback = updates.get("llm_fallback", config.profiles[profile_name].llm_fallback)
    if resulting_main == resulting_fallback:
        raise ValueError("LLM main and fallback routes must be different")

    resulting_stt_main = updates.get("stt_main", config.profiles[profile_name].stt_main)
    resulting_stt_fallback = updates.get("stt_fallback", config.profiles[profile_name].stt_fallback)
    if resulting_stt_main == resulting_stt_fallback:
        raise ValueError("STT main and fallback routes must be different")

    llm_main = updates.pop("llm_main", None)
    llm_fallback = updates.pop("llm_fallback", None)
    stt_main = updates.pop("stt_main", None)
    stt_fallback = updates.pop("stt_fallback", None)
    for field_name, value in updates.items():
        setattr(config, field_name, value)
    if llm_main is not None:
        config.profiles[profile_name].llm_main = llm_main
    if llm_fallback is not None:
        config.profiles[profile_name].llm_fallback = llm_fallback
    if stt_main is not None:
        config.profiles[profile_name].stt_main = stt_main
    if stt_fallback is not None:
        config.profiles[profile_name].stt_fallback = stt_fallback
