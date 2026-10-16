from __future__ import annotations

from runtime.models.config import VoxLogConfig
from runtime.provider_qualification import (
    LocalRuntimeInspection,
    _proxy_environment,
    _sherpa_assets_available,
    build_provider_qualification,
)


def _provider(payload: dict, provider_id: str) -> dict:
    return next(item for item in payload["providers"] if item["id"] == provider_id)


def test_qualification_separates_configuration_from_live_evidence() -> None:
    config = VoxLogConfig(
        dashscope_key_us="",
        dashscope_key_cn="",
        openai_key="secret-openai-key",
        siliconflow_key="",
    )

    payload = build_provider_qualification(
        config,
        platform="windows",
        runtime_inspector=lambda _provider_id: LocalRuntimeInspection(False, None),
    )

    assert payload["contract_version"] == "2026-07-19.2"
    assert payload["platform"] == "windows"
    assert payload["active_profile"] == "home"
    assert payload["summary"]["qualified_count"] == 0
    assert payload["summary"]["configured_count"] == 2
    assert payload["summary"]["active_routes_configured"] is False

    openai_stt = _provider(payload, "openai-whisper")
    assert openai_stt["configured"] is True
    assert openai_stt["readiness"] == "configured_unverified"
    assert openai_stt["probe_status"] == "not_run"
    assert openai_stt["active_roles"] == ["stt_fallback"]

    openai_llm = _provider(payload, "openai-gpt")
    assert openai_llm["configured"] is True
    assert openai_llm["active_roles"] == ["llm_main"]

    qwen = _provider(payload, "qwen-us")
    assert qwen["readiness"] == "missing_credential"
    assert qwen["active_roles"] == ["stt_main"]

    whisper = _provider(payload, "whispercpp-local")
    assert whisper["support"] == "install_required"
    assert whisper["readiness"] == "missing_runtime"
    assert whisper["package_available"] is False
    assert whisper["assets_available"] is None
    assert whisper["install_command"] == 'py -m pip install ".[local]"'

    doubao = _provider(payload, "doubao")
    assert doubao["support"] == "supported"
    assert doubao["readiness"] == "missing_credential"

    serialized = str(payload)
    assert "secret-openai-key" not in serialized


def test_local_runtime_can_be_qualified_without_a_secret() -> None:
    config = VoxLogConfig(
        dashscope_key_us="",
        dashscope_key_cn="",
        openai_key="",
        siliconflow_key="",
    )

    payload = build_provider_qualification(
        config,
        platform="windows",
        runtime_inspector=lambda provider_id: LocalRuntimeInspection(
            provider_id == "whispercpp-local", None
        ),
    )

    whisper = _provider(payload, "whispercpp-local")
    assert whisper["configured"] is True
    assert whisper["runtime_available"] is True
    assert whisper["readiness"] == "ready"
    assert whisper["probe_status"] == "not_run"
    assert payload["summary"]["qualified_count"] == 0
    assert "real audio smoke" in whisper["action"]


def test_sherpa_qualification_requires_asr_and_vad_assets(tmp_path, monkeypatch) -> None:
    model_dir = tmp_path / "sensevoice"
    model_dir.mkdir()
    (model_dir / "model.int8.onnx").write_bytes(b"model")
    (model_dir / "tokens.txt").write_text("token", encoding="utf-8")
    vad_model = tmp_path / "silero_vad.onnx"
    monkeypatch.setenv("VOXAGENT_SHERPA_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("VOXAGENT_SHERPA_VAD_MODEL", str(vad_model))

    assert _sherpa_assets_available() is False
    vad_model.write_bytes(b"vad")
    assert _sherpa_assets_available() is True


def test_sherpa_reports_package_and_assets_separately() -> None:
    config = VoxLogConfig()

    payload = build_provider_qualification(
        config,
        platform="windows",
        runtime_inspector=lambda provider_id: (
            LocalRuntimeInspection(True, False)
            if provider_id == "sherpa-sensevoice"
            else LocalRuntimeInspection(False, None)
        ),
    )

    sherpa = _provider(payload, "sherpa-sensevoice")
    assert sherpa["package_available"] is True
    assert sherpa["assets_available"] is False
    assert sherpa["runtime_available"] is False
    assert sherpa["readiness"] == "missing_runtime"
    assert sherpa["asset_command"] == "py scripts/fetch_sherpa_model.py"
    assert "SenseVoice and VAD model assets" in sherpa["action"]


def test_proxy_environment_is_secret_free(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.example.test:8443")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    payload = _proxy_environment()

    assert payload == {
        "httpx_trust_env": True,
        "proxy_environment_configured": True,
        "no_proxy_configured": True,
    }
    assert "secret" not in str(payload)


def test_windows_llm_adapters_report_configuration_separately_from_live_evidence() -> None:
    config = VoxLogConfig(
        doubao_key="doubao-test",
        anthropic_key="anthropic-test",
        custom_llm_base_url="https://llm.example.test/v1",
        custom_llm_model="team-model",
    )
    config.profiles["home"].llm_main = "doubao"
    config.profiles["home"].llm_fallback = "claude"

    payload = build_provider_qualification(
        config,
        platform="windows",
        runtime_inspector=lambda _provider_id: LocalRuntimeInspection(False, None),
    )

    doubao = _provider(payload, "doubao")
    claude = _provider(payload, "claude")
    custom = _provider(payload, "openai-compatible-custom")
    assert doubao["support"] == "supported"
    assert doubao["readiness"] == "configured_unverified"
    assert doubao["active_roles"] == ["llm_main"]
    assert claude["support"] == "supported"
    assert claude["active_roles"] == ["llm_fallback"]
    assert custom["support"] == "supported"
    assert custom["configured"] is True
    assert payload["summary"]["action_required_count"] == 4
