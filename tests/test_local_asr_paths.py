from __future__ import annotations

from runtime import local_asr_paths


def _write_bundle(root) -> None:
    model = root / local_asr_paths.SHERPA_BUNDLE
    model.mkdir(parents=True)
    (model / "model.int8.onnx").write_bytes(b"model")
    (model / "tokens.txt").write_text("tokens", encoding="utf-8")
    (root / local_asr_paths.VAD_FILE).write_bytes(b"vad")


def _clear_overrides(monkeypatch) -> None:
    monkeypatch.delenv("VOXAGENT_SHERPA_MODEL_DIR", raising=False)
    monkeypatch.delenv("VOXAGENT_SHERPA_VAD_MODEL", raising=False)


def test_new_vox_agent_model_path_is_canonical(tmp_path, monkeypatch) -> None:
    _clear_overrides(monkeypatch)
    primary = tmp_path / ".voxagent" / "models"
    legacy = tmp_path / ".voxlog" / "models"
    monkeypatch.setattr(local_asr_paths, "vox_agent_models_root", lambda: primary)
    monkeypatch.setattr(local_asr_paths, "legacy_voxlog_models_root", lambda: legacy)

    assert local_asr_paths.resolve_sherpa_model_dir() == primary / local_asr_paths.SHERPA_BUNDLE
    assert local_asr_paths.resolve_sherpa_vad_model() == primary / local_asr_paths.VAD_FILE


def test_complete_legacy_bundle_is_a_read_only_fallback(tmp_path, monkeypatch) -> None:
    _clear_overrides(monkeypatch)
    primary = tmp_path / ".voxagent" / "models"
    legacy = tmp_path / ".voxlog" / "models"
    _write_bundle(legacy)
    monkeypatch.setattr(local_asr_paths, "vox_agent_models_root", lambda: primary)
    monkeypatch.setattr(local_asr_paths, "legacy_voxlog_models_root", lambda: legacy)

    assert local_asr_paths.resolve_sherpa_model_dir() == legacy / local_asr_paths.SHERPA_BUNDLE
    assert local_asr_paths.resolve_sherpa_vad_model() == legacy / local_asr_paths.VAD_FILE
    assert local_asr_paths.sherpa_assets_available() is True


def test_primary_bundle_wins_when_both_exist(tmp_path, monkeypatch) -> None:
    _clear_overrides(monkeypatch)
    primary = tmp_path / ".voxagent" / "models"
    legacy = tmp_path / ".voxlog" / "models"
    _write_bundle(primary)
    _write_bundle(legacy)
    monkeypatch.setattr(local_asr_paths, "vox_agent_models_root", lambda: primary)
    monkeypatch.setattr(local_asr_paths, "legacy_voxlog_models_root", lambda: legacy)

    assert local_asr_paths.resolve_sherpa_model_dir() == primary / local_asr_paths.SHERPA_BUNDLE
    assert local_asr_paths.resolve_sherpa_vad_model() == primary / local_asr_paths.VAD_FILE
