from __future__ import annotations

import pytest

from runtime.models.config import VoxLogConfig
from runtime.provider_settings import apply_provider_settings_update


def test_provider_settings_update_applies_validated_llm_routes_and_metadata() -> None:
    config = VoxLogConfig()

    apply_provider_settings_update(
        config,
        {
            "active_profile": "home",
            "digest_enhancement_enabled": False,
            "digest_enhancement_provider": "claude",
            "doubao_key": "doubao-test",
            "anthropic_key": "anthropic-test",
            "custom_llm_key": "custom-test",
            "openai_llm_model": "gpt-4.1-mini",
            "qwen_llm_model": "qwen-plus",
            "doubao_model": "doubao-model",
            "anthropic_model": "claude-sonnet-5",
            "custom_llm_base_url": "https://llm.example.test/v1/",
            "custom_llm_model": "team-model",
            "ollama_model": "qwen3:8b",
            "llm_main": "doubao",
            "llm_fallback": "claude",
            "stt_main": "sherpa-sensevoice",
            "stt_fallback": "openai-whisper",
        },
    )

    assert config.digest_enhancement_enabled is False
    assert config.digest_enhancement_provider == "claude"
    assert config.doubao_key == "doubao-test"
    assert config.custom_llm_base_url == "https://llm.example.test/v1"
    assert config.custom_llm_model == "team-model"
    assert config.profiles["home"].llm_main == "doubao"
    assert config.profiles["home"].llm_fallback == "claude"
    assert config.profiles["home"].stt_main == "sherpa-sensevoice"
    assert config.profiles["home"].stt_fallback == "openai-whisper"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("active_profile", "missing"),
        ("llm_main", "arbitrary-provider"),
        ("llm_fallback", "openai-gpt"),
        ("custom_llm_base_url", "file:///tmp/model"),
        ("custom_llm_base_url", "http://llm.example.test/v1"),
        ("custom_llm_base_url", "https://user:secret@example.test/v1"),
        ("digest_enhancement_enabled", "false"),
        ("doubao_key", "x" * 20_001),
        ("unknown_field", "value"),
    ],
)
def test_provider_settings_update_rejects_invalid_values_atomically(field: str, value: object) -> None:
    config = VoxLogConfig(doubao_key="original")
    patch = {
        "doubao_key": "replacement",
        field: value,
    }
    if field == "custom_llm_base_url":
        patch["custom_llm_model"] = "team-model"

    with pytest.raises(ValueError):
        apply_provider_settings_update(config, patch)

    assert config.doubao_key == "original"


def test_provider_settings_rejects_ambiguous_or_unknown_stt_routes_atomically() -> None:
    config = VoxLogConfig(doubao_key="original")

    with pytest.raises(ValueError, match="STT main and fallback"):
        apply_provider_settings_update(
            config,
            {
                "doubao_key": "replacement",
                "stt_main": "openai-whisper",
                "stt_fallback": "openai-whisper",
            },
        )
    assert config.doubao_key == "original"

    with pytest.raises(ValueError, match="stt_main"):
        apply_provider_settings_update(config, {"stt_main": "legacy-magic-preview"})
