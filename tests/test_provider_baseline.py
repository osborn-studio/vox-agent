"""T28a: every macOS baseline speech provider has an explicit Windows answer.

The macOS registry is read from source, so a provider added there without a
Windows mapping or GAP fails here. Nothing in the Windows matrix may claim to
be qualified without probe evidence (ADR 2026-07-19 provider qualification and
Windows ASR qualification).
"""

from __future__ import annotations

import re
from pathlib import Path

from runtime.models.config import VoxLogConfig
from runtime.provider_baseline import BASELINE, GAP_REASONS, macos_registry, windows_answer
from runtime.provider_qualification import PROVIDERS, LocalRuntimeInspection, build_provider_qualification

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "macos/VoxLog/ASR/ASRProviderRegistry.swift"


def test_the_registry_parser_reads_implemented_and_config_only_providers():
    implemented, config_only = macos_registry(REGISTRY.read_text(encoding="utf-8"))
    assert {"apple", "openai", "bailian", "bailianUS", "sherpa"} <= implemented
    assert {"azure", "google", "custom"} <= config_only
    assert not implemented & config_only


def test_an_entry_the_parser_cannot_classify_fails_loudly():
    import pytest

    source = REGISTRY.read_text(encoding="utf-8")
    mutated = source.replace("createClient: { VolcASRClient() }", "createClient: makeVolcano")
    assert mutated != source
    with pytest.raises(ValueError):
        macos_registry(mutated)
    # A provider added in another form (a helper call) must not be skipped.
    helper = source.replace("        return dict", "        dict[.newProvider] = makeProviderEntry()\n        return dict")
    assert helper != source
    with pytest.raises(ValueError):
        macos_registry(helper)
    twice = source.replace("        return dict", "        dict[.openai] = ProviderEntry(configType: X.self, createClient: nil)\n        return dict")
    with pytest.raises(ValueError):
        macos_registry(twice)


def test_every_macos_provider_has_a_windows_mapping_or_an_explicit_gap():
    implemented, config_only = macos_registry(REGISTRY.read_text(encoding="utf-8"))
    assert set(BASELINE) == implemented | config_only, "baseline must list exactly the macOS registry"
    windows_ids = {spec.id for spec in PROVIDERS}
    for macos_id, entry in BASELINE.items():
        if entry.windows_id:
            assert entry.windows_id in windows_ids, macos_id
            assert entry.gap is None, macos_id
        else:
            assert entry.gap in GAP_REASONS, macos_id
            assert entry.follow_up, f"{macos_id}: a GAP names its follow-up task"
        assert entry.macos_implemented == (macos_id in implemented), macos_id


def test_config_only_macos_providers_are_not_promised_on_windows():
    _, config_only = macos_registry(REGISTRY.read_text(encoding="utf-8"))
    for macos_id in config_only:
        assert BASELINE[macos_id].gap == "not_implemented_on_macos", macos_id


def test_apple_speech_is_platform_unavailable_and_deferred_cloud_is_visible():
    assert BASELINE["apple"].gap == "platform_unavailable"
    assert windows_answer("apple") == "GAP: platform_unavailable (macOS-only Apple Speech)"
    deferred = [m for m, e in BASELINE.items() if e.gap == "deferred_cloud"]
    assert {"volcano", "deepgram", "assemblyai", "elevenlabs", "soniox", "baidu"} <= set(deferred)
    deferred_spec = next(spec for spec in PROVIDERS if spec.id == "additional-cloud-asr")
    assert deferred_spec.support == "deferred", "the matrix shows the deferred family"


def test_local_macos_qwen_maps_to_the_experimental_windows_path():
    entry = BASELINE["sherpa"]
    assert entry.windows_id == "qwen-local"
    assert "sherpa-sensevoice" in entry.note, "the recommended Windows local path is named"


def test_nothing_is_qualified_without_probe_evidence():
    config = VoxLogConfig(openai_key="key", dashscope_key_us="key")
    payload = build_provider_qualification(
        config,
        platform="windows",
        runtime_inspector=lambda _provider: LocalRuntimeInspection(True, True),
    )
    assert payload["summary"]["qualified_count"] == 0
    for provider in payload["providers"]:
        assert provider["probe_status"] in {"not_run", "not_applicable"}, provider["id"]
        assert provider["readiness"] != "configured_unverified" or provider["transport"] == "cloud"


def test_the_contract_document_lists_every_baseline_provider():
    doc = (ROOT / "docs/verification/windows-provider-contract.md").read_text(encoding="utf-8")
    for macos_id in BASELINE:
        assert re.search(rf"`{re.escape(macos_id)}`", doc), macos_id
    assert "2026-07-19-windows-asr-qualification.md" in doc
    assert "2026-07-19-provider-qualification-contract.md" in doc
