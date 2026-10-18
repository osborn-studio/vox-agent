"""Tests for the L0 LLM registry reconciliation tooling (no runtime change).

The registry fixtures under ``tests/fixtures/llm_registry_l0/`` are verbatim
copies of Osborn-PA's ``packages/llm-registry`` files (see PROVENANCE.md there).
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import export_llm_provider_inventory as exporter
from scripts import reconcile_llm_registry as reconcile
from scripts.llm_registry_l0 import (
    SUPPORTED_SCHEMAS,
    UNSUPPORTED,
    Patch,
    RegistrySchemaError,
    build_url,
    check_identity_bindings,
    encode_model,
    load_baseline,
    load_registry,
    parse_providers,
    validate_models_document,
    validate_providers_document,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/llm_registry_l0"
SNAPSHOT = ROOT / "docs/llm-registry/vox-provider-inventory.json"
PYTHON = sys.executable


@pytest.fixture(scope="module")
def inventory() -> dict:
    return json.loads(SNAPSHOT.read_text())


@pytest.fixture(scope="module")
def pa_docs() -> dict:
    return {
        name: json.loads((FIXTURES / f"{name}.json").read_text())
        for name in ("providers", "models", "urls", "baseline-v1")
    }


@pytest.fixture(scope="module")
def pa_report() -> dict:
    return reconcile.run(
        SNAPSHOT,
        FIXTURES / "providers.json",
        FIXTURES / "models.json",
        FIXTURES / "urls.json",
        FIXTURES / "baseline-v1.json",
    )


def _write_registry(tmp_path: Path, providers_doc: dict, models_doc: dict):
    providers_path = tmp_path / "providers.json"
    models_path = tmp_path / "models.json"
    providers_path.write_text(json.dumps(providers_doc))
    models_path.write_text(json.dumps(models_doc))
    return load_registry(providers_path, models_path)


def _endpoint(base_url: str, chat_path: str = "/chat/completions", **dialect) -> dict:
    endpoint = {"base_url": base_url, "chat_path": chat_path, "discovery_path": "/models"}
    if dialect:
        endpoint["dialect"] = dialect
    return endpoint


def _provider(provider_id: str, **overrides) -> dict:
    base = {
        "id": provider_id,
        "label": {"zh": provider_id, "en": provider_id},
        "region": "intl",
        "editions": ["global"],
        "aliases": {"vox": provider_id, "vox_keychain_account": f"tf_llm_{provider_id}"},
        "secret": {"id": provider_id, "env": [f"{provider_id.upper()}_API_KEY"], "required": True},
        "auth": {"type": "api-key", "header": "Authorization", "prefix": "Bearer "},
        "endpoints": {"openai-chat-completions": _endpoint("https://example.invalid/v1")},
        "default_endpoint": "openai-chat-completions",
        "status": "active",
    }
    base.update(overrides)
    return base


def _doc(*providers: dict) -> dict:
    return {
        "schema": SUPPORTED_SCHEMAS["providers"],
        "updated_at": "2026-10-18",
        "endpoint_types": ["openai-chat-completions", "anthropic-messages"],
        "providers": list(providers),
    }


def _models(table: dict[str, dict[str, dict]]) -> dict:
    return {
        "schema": SUPPORTED_SCHEMAS["models"],
        "meta": {
            "source": "https://models.dev/api.json",
            "fetched_at": "2026-10-18",
            "usd_cny": 7.1,
            "generator": "test",
        },
        "providers": table,
    }


def _vox(inventory: dict, vox_id: str) -> dict:
    return next(p for p in inventory["macos"]["providers"] if p["id"] == vox_id)


# ---------------------------------------------------------------------------
# PA fixtures: provenance, structural validity, identity bindings, URL rule
# ---------------------------------------------------------------------------


def test_fixtures_carry_provenance():
    note = (FIXTURES / "PROVENANCE.md").read_text()
    assert "codex/llm-provider-learnings" in note
    assert "af7dfe66d3fad1341331925f3029fa79ef5b3965" in note
    for name in ("providers", "models", "urls", "baseline-v1", "providers.schema", "models.schema"):
        assert (FIXTURES / f"{name}.json").exists(), name


def test_pa_documents_pass_the_structural_check(pa_docs):
    assert validate_providers_document(pa_docs["providers"]) == []
    assert validate_models_document(pa_docs["models"]) == []
    assert pa_docs["providers"]["schema"] == SUPPORTED_SCHEMAS["providers"]
    assert pa_docs["models"]["schema"] == SUPPORTED_SCHEMAS["models"]
    assert len(pa_docs["providers"]["providers"]) == 15
    assert sum(len(m) for m in pa_docs["models"]["providers"].values()) == 451


def test_structural_check_mirrors_schema_required_fields_and_patterns(pa_docs):
    broken = copy.deepcopy(pa_docs["providers"])
    first = broken["providers"][0]
    del first["status"]
    first["id"] = "Bad-Id"
    first["endpoints"]["openai-chat-completions"]["base_url"] = "https://host/path/"
    first["endpoints"]["openai-chat-completions"]["dialect"]["thinking_disable"] = "enable_thinking"
    first["extra"] = 1
    problems = validate_providers_document(broken)
    assert any("missing status" in p for p in problems)
    assert any("id does not match" in p for p in problems)
    assert any("violates the schema pattern" in p for p in problems)
    assert any("thinking_disable is not a patch" in p for p in problems)
    assert any("unexpected key extra" in p for p in problems)

    broken_models = copy.deepcopy(pa_docs["models"])
    (model_id, spec) = next(iter(broken_models["providers"]["openai"].items()))
    del spec["hint_zh"]
    spec["context"] = True
    problems = validate_models_document(broken_models)
    assert any(f"openai/{model_id} missing hint_zh" in p for p in problems)
    assert any(f"openai/{model_id} context is not a positive integer" in p for p in problems)


def test_pa_providers_honour_frozen_identity_bindings(pa_docs):
    assert check_identity_bindings(pa_docs["providers"], pa_docs["baseline-v1"]) == []
    mutated = copy.deepcopy(pa_docs["providers"])
    for provider in mutated["providers"]:
        if provider["id"] == "claude":
            provider["secret"]["env"] = ["CLAUDE_KEY"]
            provider["endpoints"]["anthropic-messages"]["base_url"] = "https://api.anthropic.com/v1"
        if provider["id"] == "kimi_cn":
            provider["secret"]["id"] = "moonshot_cn"
    mutated["providers"] = [p for p in mutated["providers"] if p["id"] != "ollama"]
    problems = check_identity_bindings(mutated, pa_docs["baseline-v1"])
    assert "claude: secret.env order changed" in problems
    assert any(
        p.startswith("claude: endpoint anthropic-messages base_url changed") for p in problems
    )
    assert "kimi_cn: secret.id changed to 'moonshot_cn'" in problems
    assert "ollama: frozen identity but provider is gone" in problems


def test_url_rule_reproduces_every_pa_url_fixture(pa_report, pa_docs):
    check = pa_report["url_rule_check"]
    assert check["failures"] == []
    assert check["checked"] == len(pa_docs["urls"]) == 13


def test_url_rule_is_string_concatenation_with_encoded_model():
    assert encode_model("anthropic/claude-3.5") == "anthropic%2Fclaude-3.5"
    assert encode_model("qwen-flash!~*'()") == "qwen-flash!~*'()"
    assert (
        build_url("https://h/v1beta", "/models/{model}:streamGenerateContent?alt=sse", "a/b c")
        == "https://h/v1beta/models/a%2Fb%20c:streamGenerateContent?alt=sse"
    )
    # no URL resolution: a base without a trailing slash and a path are simply joined
    assert (
        build_url("https://api.anthropic.com", "/v1/messages", None)
        == "https://api.anthropic.com/v1/messages"
    )
    with pytest.raises(RegistrySchemaError, match="needs a model id"):
        build_url("https://h", "/models/{model}", None)


# ---------------------------------------------------------------------------
# Reader behaviour
# ---------------------------------------------------------------------------


def test_reader_refuses_unknown_schema_constant(pa_docs):
    bad = copy.deepcopy(pa_docs["providers"])
    bad["schema"] = "osborn-llm-registry/providers@2"
    with pytest.raises(RegistrySchemaError, match="providers@2"):
        parse_providers(bad)
    with pytest.raises(RegistrySchemaError, match="compat"):
        load_baseline(FIXTURES / "providers.json")


def test_reader_treats_unknown_enums_and_dialect_keys_as_unsupported(tmp_path):
    provider = _provider(
        "future",
        region="mars",
        status="preview",
        auth={"type": "oauth"},
        endpoints={
            "openai-chat-completions": _endpoint(
                "https://example.invalid/v1", thinking_disable=None, holographic=True
            ),
            "quantum-messages": _endpoint("https://example.invalid/q", "/q"),
        },
    )
    registry = _write_registry(tmp_path, _doc(provider), _models({}))
    future = registry.providers["future"]
    assert future.region == future.status == future.auth_type == UNSUPPORTED
    chat = future.endpoints["openai-chat-completions"]
    assert chat.unknown_dialect_keys == frozenset({"holographic"})
    assert chat.specified_dialect_keys == frozenset({"thinking_disable"})
    assert chat.thinking_disable is None
    assert chat.stream_options is True and chat.reasoning_split is False  # schema defaults
    assert future.endpoints["quantum-messages"].known_type is False


def test_reader_parses_typed_thinking_patch_and_rejects_malformed_ones(tmp_path):
    provider = _provider(
        "zhipu",
        endpoints={
            "openai-chat-completions": _endpoint(
                "https://open.bigmodel.cn/api/paas/v4",
                thinking_disable={"path": "reasoning_effort", "value": "none"},
            )
        },
    )
    registry = _write_registry(tmp_path, _doc(provider), _models({}))
    endpoint = registry.providers["zhipu"].endpoints["openai-chat-completions"]
    assert endpoint.thinking_disable == Patch("reasoning_effort", "none")
    bad = _provider(
        "x",
        endpoints={"openai-chat-completions": _endpoint("https://x", thinking_disable="thinking")},
    )
    with pytest.raises(RegistrySchemaError, match="patch"):
        _write_registry(tmp_path, _doc(bad), _models({}))
    bad = _provider("x", default_endpoint="anthropic-messages")
    with pytest.raises(RegistrySchemaError, match="default_endpoint"):
        _write_registry(tmp_path, _doc(bad), _models({}))


def test_reader_refuses_plain_http_urls():
    with pytest.raises(RegistrySchemaError, match="https"):
        load_registry(
            "http://registry.invalid/providers.json", "http://registry.invalid/models.json"
        )


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_agreed_id_mapping_table():
    assert reconcile.AGREED_VOX_TO_REGISTRY == {
        "bailian": "dashscope_cn",
        "bailianUS": "dashscope_us",
        "kimi": "kimi_intl",
        "minimaxCN": "minimax_cn",
        "minimaxIntl": "minimax_intl",
        "claude": "claude",
        "ollama": "ollama",
    }
    for same in ("doubao", "openai", "gemini", "deepseek", "zhipu", "openrouter"):
        assert reconcile.agreed_registry_id(same) == same


def test_pa_registry_maps_every_vox_provider_through_aliases(pa_report):
    rows = {row["vox_id"]: row for row in pa_report["id_mapping"]}
    assert len(rows) == 13
    assert all(row["source"] == "aliases.vox" for row in rows.values())
    assert not any(row["disagrees_with_agreed_table"] for row in rows.values())
    assert rows["kimi"]["registry_id"] == "kimi_intl"
    assert rows["bailian"]["registry_id"] == "dashscope_cn"
    assert rows["bailianUS"]["registry_id"] == "dashscope_us"
    assert rows["minimaxCN"]["registry_id"] == "minimax_cn"
    assert pa_report["providers_vox_only"] == []
    assert pa_report["providers_registry_only"] == ["dashscope_intl", "kimi_cn"]
    assert pa_report["status_warnings"] == []


def test_map_providers_falls_back_to_agreed_table_and_flags_disagreement(tmp_path, inventory):
    registry = _write_registry(
        tmp_path,
        _doc(
            _provider("kimi_global", aliases={"vox": "kimi"}),  # disagrees with kimi -> kimi_intl
            _provider("minimax_cn", aliases={}),  # agreed-table fallback
            _provider("deepseek", aliases={}),  # same-name fallback
            _provider("openai", aliases={"vox": "openai"}, status="deprecated"),
        ),
        _models({}),
    )
    rows = {row["vox_id"]: row for row in reconcile.map_providers(inventory, registry)}
    assert rows["kimi"]["registry_id"] == "kimi_global"
    assert rows["kimi"]["disagrees_with_agreed_table"] is True
    assert rows["minimaxCN"]["source"] == "agreed-table"
    assert rows["deepseek"]["source"] == "same-name"
    assert rows["doubao"]["source"] == "unmapped" and rows["doubao"]["registry_id"] is None
    report = reconcile.reconcile(inventory, registry)
    assert report["status_warnings"] == [
        {"vox_id": "openai", "registry_id": "openai", "status": "deprecated"}
    ]
    assert report["summary"]["mapping_disagreements"] == 1


def test_duplicate_vox_alias_is_an_error(tmp_path, inventory):
    registry = _write_registry(
        tmp_path,
        _doc(_provider("a", aliases={"vox": "openai"}), _provider("b", aliases={"vox": "openai"})),
        _models({}),
    )
    with pytest.raises(reconcile.ReconcileError, match="claimed by both"):
        reconcile.map_providers(inventory, registry)


# ---------------------------------------------------------------------------
# Difference classes against PA's files
# ---------------------------------------------------------------------------


def test_pa_effective_chat_urls_match_and_claude_split_is_informational(pa_report):
    assert pa_report["chat_url_mismatches"] == []
    [claude] = pa_report["base_url_only_differences"]
    assert claude["vox_id"] == "claude"
    assert claude["vox_base_url"] == "https://api.anthropic.com/v1"
    assert claude["registry_base_url"] == "https://api.anthropic.com"
    assert claude["registry_chat_path"] == "/v1/messages"
    assert claude["effective_url"] == "https://api.anthropic.com/v1/messages"
    [gemini] = pa_report["default_endpoint_differences"]
    assert gemini["vox_id"] == "gemini"
    assert gemini["registry_default_endpoint"] == "gemini-generate-content"
    assert "/v1beta/openai" in gemini["note"]


def test_pa_dialects_agree_with_vox_thinking_fields(pa_report):
    assert pa_report["dialect_disagreements"] == []
    assert pa_report["dialect_notes"] == []
    assert reconcile.vox_thinking_patch("thinking") == {
        "path": "thinking.type",
        "value": "disabled",
    }
    assert reconcile.vox_thinking_patch("enable_thinking") == {
        "path": "enable_thinking",
        "value": False,
    }
    assert reconcile.vox_thinking_patch("reasoning_effort") == {
        "path": "reasoning_effort",
        "value": "none",
    }
    assert reconcile.vox_thinking_patch("think") == {"path": "think", "value": False}
    assert reconcile.vox_thinking_patch(None) is None


def test_pa_models_gaps_in_both_directions(pa_report):
    missing = pa_report["models_missing_in_registry"]
    assert set(missing) == {"bailianUS", "doubao", "kimi", "zhipu"}
    assert missing["zhipu"] == ["glm-4-long", "glm-5-turbo"]
    assert missing["bailianUS"] == ["qwen-long"]
    assert "kimi-k2.5" in missing["kimi"]
    assert len(missing["doubao"]) == 6  # PA has no doubao catalogue yet
    assert "deepseek" not in missing and "openai" not in missing and "bailian" not in missing
    not_compared = {row["vox_id"]: row["reason"] for row in pa_report["models_not_compared"]}
    assert not_compared == {
        "doubao": "no models.json entry",
        "ollama": "no models.json entry",
        "openrouter": "Vox lists no modelOptions and accepts any model id",
    }
    assert pa_report["models_missing_in_vox"]["deepseek"] == [
        "deepseek-flash",
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
        "deepseek-v4-pro",
    ]
    assert pa_report["summary"]["models_missing_in_registry"] == 13


def test_pa_secret_and_keychain_mapping(pa_report):
    rows = {row["vox_id"]: row for row in pa_report["secrets"]}
    assert all(row["keychain_alias_matches"] for row in rows.values())
    assert all(row["auth_requirement_matches"] for row in rows.values())
    kimi = rows["kimi"]
    assert kimi["secret_id"] == kimi["new_account"] == "kimi_intl"
    assert kimi["secret_env"] == ["MOONSHOT_INTL_API_KEY", "MOONSHOT_API_KEY"]
    assert kimi["legacy_services"] == ["com.voxlog.scalar", "com.voxlog.grouped"]
    assert kimi["legacy_account"] == "tf_llm_kimi"
    assert kimi["new_service"] == "osborn.llm"
    ollama = rows["ollama"]
    assert ollama["secret_required"] is False and ollama["requires_api_key"] is False
    assert ollama["auth_type"] == "none" and ollama["secret_env"] == []


def test_pa_windows_routes_leave_only_the_ollama_loopback_difference(pa_report):
    rows = {row["route_id"]: row for row in pa_report["windows_routes"]}
    assert set(rows) == {
        "openai-gpt",
        "qwen-turbo",
        "doubao",
        "claude",
        "openai-compatible-custom",
        "ollama",
    }
    for route_id in ("openai-gpt", "qwen-turbo", "doubao", "claude", "openai-compatible-custom"):
        assert rows[route_id]["issues"] == [], route_id
    assert rows["claude"]["protocol"] == "anthropic"
    assert rows["openai-compatible-custom"]["user_configured_endpoint"] is True
    [issue] = rows["ollama"]["issues"]
    assert "http://localhost:11434/v1/chat/completions" in issue
    assert "http://127.0.0.1:11434/v1/chat/completions" in issue


def test_pa_summary_and_strict_verdict(pa_report):
    summary = pa_report["summary"]
    assert summary["providers_vox_only"] == 0
    assert summary["providers_registry_only"] == 2
    assert summary["chat_url_mismatches"] == 0
    assert summary["base_url_only_differences"] == 1
    assert summary["default_endpoint_differences"] == 1
    assert summary["dialect_disagreements"] == 0
    assert summary["keychain_alias_mismatches"] == 0
    assert summary["windows_route_issues"] == 1
    assert summary["url_rule_failures"] == 0
    assert summary["identity_binding_violations"] == 0
    assert pa_report["has_differences"] is True
    informational_only = {key: 0 for key in summary}
    informational_only["base_url_only_differences"] = 1
    informational_only["default_endpoint_differences"] = 1
    assert reconcile.has_differences(informational_only) is False
    assert reconcile.has_differences({**informational_only, "chat_url_mismatches": 1}) is True


# ---------------------------------------------------------------------------
# Difference classes with synthetic registries
# ---------------------------------------------------------------------------


def test_chat_url_mismatch_and_missing_endpoint(tmp_path, inventory):
    registry = _write_registry(
        tmp_path,
        _doc(
            # same effective URL, base split differently -> informational only
            _provider(
                "openai",
                endpoints={
                    "openai-chat-completions": _endpoint(
                        "https://api.openai.com", "/v1/chat/completions"
                    )
                },
            ),
            # different host -> mismatch
            _provider(
                "deepseek",
                endpoints={"openai-chat-completions": _endpoint("https://api.deepseek.cn")},
            ),
            # Vox speaks anthropic-messages for claude; registry declares only the OpenAI one
            _provider("claude"),
        ),
        _models({}),
    )
    report = reconcile.reconcile(inventory, registry)
    rows = {row["vox_id"]: row for row in report["chat_url_mismatches"]}
    assert set(rows) == {"deepseek", "claude"}
    assert rows["deepseek"]["vox"] == "https://api.deepseek.com/chat/completions"
    assert rows["deepseek"]["registry"] == "https://api.deepseek.cn/chat/completions"
    assert rows["claude"]["endpoint"] == "anthropic-messages"
    assert rows["claude"]["reason"] == "endpoint not declared in registry"
    [openai] = report["base_url_only_differences"]
    assert openai["effective_url"] == "https://api.openai.com/v1/chat/completions"
    assert report["summary"]["chat_url_mismatches"] == 2


def test_dialect_disagreements_use_the_typed_patch(tmp_path, inventory):
    registry = _write_registry(
        tmp_path,
        _doc(
            # Vox: reasoning_effort -> registry says thinking.type -> disagreement
            _provider(
                "zhipu",
                endpoints={
                    "openai-chat-completions": _endpoint(
                        "https://open.bigmodel.cn/api/paas/v4",
                        thinking_disable={"path": "thinking.type", "value": "disabled"},
                    )
                },
            ),
            # Vox: needs_reasoning_split true; registry omits the key (default false)
            _provider(
                "minimax_cn",
                aliases={"vox": "minimaxCN"},
                endpoints={"openai-chat-completions": _endpoint("https://api.minimaxi.com/v1")},
            ),
            # Vox: nil; registry explicit null -> agreement
            _provider(
                "openai",
                endpoints={
                    "openai-chat-completions": _endpoint(
                        "https://api.openai.com/v1", thinking_disable=None
                    )
                },
            ),
            # Vox: thinking (thinking.type=disabled); registry omits -> differs from default
            _provider(
                "deepseek",
                endpoints={"openai-chat-completions": _endpoint("https://api.deepseek.com")},
            ),
        ),
        _models({}),
    )
    report = reconcile.reconcile(inventory, registry)
    rows = {(r["vox_id"], r["field"]): r for r in report["dialect_disagreements"]}
    assert set(rows) == {
        ("zhipu", "thinking_disable"),
        ("minimaxCN", "reasoning_split"),
        ("deepseek", "thinking_disable"),
    }
    zhipu = rows[("zhipu", "thinking_disable")]
    assert zhipu["vox"] == {"path": "reasoning_effort", "value": "none"}
    assert zhipu["registry"] == {"path": "thinking.type", "value": "disabled"}
    assert zhipu["registry_specified"] is True
    assert rows[("minimaxCN", "reasoning_split")]["registry"] is False
    assert rows[("minimaxCN", "reasoning_split")]["registry_specified"] is False
    assert rows[("deepseek", "thinking_disable")]["registry"] is None
    assert report["summary"]["dialect_disagreements"] == 3


def test_models_missing_catalogue_entry(tmp_path, inventory):
    registry = _write_registry(tmp_path, _doc(_provider("zhipu")), _models({}))
    report = reconcile.reconcile(inventory, registry)
    vox_models = [o["value"] for o in _vox(inventory, "zhipu")["model_options"]]
    assert report["models_missing_in_registry"]["zhipu"] == sorted(vox_models)
    assert report["models_not_compared"] == [
        {"vox_id": "zhipu", "registry_id": "zhipu", "reason": "no models.json entry"}
    ]


def test_secret_mapping_flags_alias_and_auth_requirement_mismatches(tmp_path, inventory):
    registry = _write_registry(
        tmp_path,
        _doc(
            _provider("zhipu", aliases={"vox": "zhipu", "vox_keychain_account": "tf_llm_glm"}),
            _provider(
                "ollama",
                region="local",
                secret={"id": "ollama", "env": [], "required": True},
                auth={"type": "none"},
            ),
        ),
        _models({}),
    )
    report = reconcile.reconcile(inventory, registry)
    rows = {row["vox_id"]: row for row in report["secrets"]}
    assert rows["zhipu"]["keychain_alias_matches"] is False
    assert rows["ollama"]["auth_requirement_matches"] is False  # Vox: no key; registry: required
    assert report["summary"]["keychain_alias_mismatches"] == 1
    assert report["summary"]["auth_requirement_mismatches"] == 1


def test_windows_routes_against_synthetic_registry(tmp_path, inventory):
    registry = _write_registry(
        tmp_path,
        _doc(
            _provider(
                "openai",
                endpoints={"openai-chat-completions": _endpoint("https://api.openai.com/v1")},
            ),
            _provider(
                "dashscope_cn",
                aliases={"vox": "bailian"},
                endpoints={
                    "openai-chat-completions": _endpoint(
                        "https://dashscope.aliyuncs.com/compatible-mode/v1"
                    )
                },
            ),
            _provider(
                "dashscope_us",
                aliases={"vox": "bailianUS"},
                endpoints={
                    "openai-chat-completions": _endpoint("https://dashscope-intl.example/v1")
                },
            ),
            _provider(
                "claude",
                endpoints={
                    "anthropic-messages": _endpoint("https://api.anthropic.com", "/v1/messages")
                },
                default_endpoint="anthropic-messages",
                status="deprecated",
            ),
        ),
        _models({}),
    )
    report = reconcile.reconcile(inventory, registry)
    rows = {row["route_id"]: row for row in report["windows_routes"]}
    assert rows["openai-gpt"]["issues"] == []
    assert rows["claude"]["issues"] == ["claude status is deprecated"]
    assert rows["doubao"]["issues"] == ["doubao missing from registry"]
    assert rows["ollama"]["issues"] == ["ollama missing from registry"]
    [qwen_issue] = rows["qwen-turbo"]["issues"]
    assert qwen_issue.startswith(
        "dashscope_us openai-chat-completions chat URL "
        "https://dashscope-intl.example/v1/chat/completions"
    )
    assert "https://dashscope-us.aliyuncs.com/compatible-mode/v1/chat/completions" in qwen_issue
    assert report["summary"]["windows_route_issues"] == 4


def test_url_rule_check_reports_failures(tmp_path, inventory):
    registry = _write_registry(tmp_path, _doc(_provider("openai")), _models({}))
    chat = {"provider": "openai", "endpoint": "openai-chat-completions"}
    fixture = [
        {
            **chat,
            "which": "chat",
            "model": "m",
            "url": "https://example.invalid/v1/chat/completions",
        },
        {**chat, "which": "discovery", "model": None, "url": "https://wrong/models"},
        {**chat, "provider": "nobody", "which": "chat", "model": "m", "url": "x"},
        {**chat, "which": "stream_chat", "model": "m", "url": "x"},
    ]
    report = reconcile.reconcile(inventory, registry, urls_fixture=fixture)
    check = report["url_rule_check"]
    assert check["checked"] == 3
    reasons = sorted(f["reason"] for f in check["failures"])
    assert reasons == ["URL differs", "URL differs", "provider/endpoint not found"]
    assert report["summary"]["url_rule_failures"] == 3


# ---------------------------------------------------------------------------
# Rendering and CLI
# ---------------------------------------------------------------------------


def test_markdown_render_has_every_section(pa_report):
    text = reconcile.render_markdown(pa_report)
    for heading in (
        "## Summary",
        "## Provider id mapping",
        "## Providers in Vox but not in the registry",
        "## Providers in the registry but not in Vox",
        "## Effective chat URL mismatches",
        "### Base URL split differently, same effective URL (informational)",
        "## Models in Vox `modelOptions` missing from `models.json`",
        "## Models in `models.json` missing from Vox `modelOptions`",
        "## Dialect disagreements",
        "## Secret id and Keychain account mapping",
        "## Engine / Windows `LlmRoute` allowlist",
        "## Registry self-checks",
    ):
        assert heading in text, heading
    assert "- `dashscope_intl`" in text and "- `kimi_cn`" in text
    assert "`tf_llm_kimi`" in text and "`osborn.llm`" in text
    assert "13 fixture URLs reproduced, 0 failures" in text
    assert "honours every frozen binding" in text
    assert "/v1beta/openai" in text


def test_cli_json_strict_and_error_exit_codes(tmp_path, capsys):
    out = tmp_path / "report.json"
    assert reconcile.main(["--json", "--output", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["report_version"] == reconcile.REPORT_VERSION
    assert payload["providers_registry_only"] == ["dashscope_intl", "kimi_cn"]
    assert payload["registry"]["provider_count"] == 15
    assert reconcile.main(["--strict", "--output", str(tmp_path / "r.md")]) == 1
    assert reconcile.main(["--providers", str(tmp_path / "missing.json")]) == 2
    assert "ERROR" in capsys.readouterr().err
    # self-checks can be skipped
    argv = ["--json", "--urls", "none", "--baseline", "none", "--output", str(out)]
    assert reconcile.main(argv) == 0
    payload = json.loads(out.read_text())
    assert payload["url_rule_check"] is None and payload["identity_check"] is None


# ---------------------------------------------------------------------------
# Inventory exporter
# ---------------------------------------------------------------------------


def test_inventory_snapshot_matches_sources_regex_mode():
    inventory = exporter.build_inventory(ROOT, "regex")
    assert exporter.check_snapshot(SNAPSHOT, inventory) == []


def test_inventory_check_cli_fails_on_stale_snapshot(tmp_path):
    stale = tmp_path / "stale.json"
    stale.write_text("{}\n")
    script = str(ROOT / "scripts/export_llm_provider_inventory.py")
    result = subprocess.run(
        [PYTHON, script, "--check", "--swift-mode", "regex", "--output", str(stale)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    assert result.returncode == 1
    assert "STALE" in result.stderr
    ok = subprocess.run(
        [PYTHON, script, "--check", "--swift-mode", "regex"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    assert ok.returncode == 0, ok.stderr
    assert "OK" in ok.stdout


def test_inventory_covers_both_surfaces(inventory):
    macos_ids = [p["id"] for p in inventory["macos"]["providers"]]
    assert macos_ids == sorted(macos_ids)
    assert set(macos_ids) == {
        "bailian",
        "bailianUS",
        "claude",
        "deepseek",
        "doubao",
        "gemini",
        "kimi",
        "minimaxCN",
        "minimaxIntl",
        "ollama",
        "openai",
        "openrouter",
        "zhipu",
    }
    assert inventory["engine_windows"]["route_ids"] == [
        "openai-gpt",
        "qwen-turbo",
        "doubao",
        "claude",
        "openai-compatible-custom",
        "ollama",
    ]
    assert inventory["macos"]["keychain"]["services"] == ["com.voxlog.scalar", "com.voxlog.grouped"]
    assert inventory["macos"]["keychain"]["accounts"]["bailianUS"] == "tf_llm_bailianUS"
    assert inventory["macos"]["chat_path_rule"]["anthropic"] == "/messages"
    assert inventory["macos"]["chat_path_rule"]["openai_compatible"] == "/chat/completions"
    assert inventory["engine_windows"]["chat_path_rule"]["anthropic"] == "/messages"
    gemini = _vox(inventory, "gemini")
    assert gemini["default_base_url"].endswith("/v1beta/openai")
    assert _vox(inventory, "zhipu")["thinking_disable_field"] == "reasoning_effort"
    assert _vox(inventory, "minimaxCN")["needs_reasoning_split"] is True
    assert _vox(inventory, "ollama")["requires_api_key"] is False
    assert _vox(inventory, "ollama")["translation"]["accepts_any_model"] is True
    bailian = _vox(inventory, "bailian")["translation"]
    assert bailian["supported_beyond_model_options"] == ["qwen3.8-omni-flash"]
    assert "gpt-4o" in _vox(inventory, "openai")["translation"]["supported_models"]
    assert "timestamp" not in json.dumps(inventory)


def test_regex_fallback_refuses_unknown_shapes(tmp_path):
    root = tmp_path / "repo"
    for rel in (
        exporter.SWIFT_PROVIDER_FILE,
        exporter.SWIFT_TRANSLATION_FILE,
        exporter.SWIFT_KEYCHAIN_FILE,
        exporter.SWIFT_RESOLVER_FILE,
    ):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, target)
    translation = root / exporter.SWIFT_TRANSLATION_FILE
    original = translation.read_text()
    needle = 'return provider == .deepseek && ["deepseek-flash", "deepseek-v4-pro"].contains(model)'
    text = original.replace(
        needle, 'if model.hasSuffix("-preview") { return true }\n        ' + needle
    )
    assert text != original
    translation.write_text(text)
    with pytest.raises(exporter.InventoryError, match="shape the regex fallback"):
        exporter.extract_swift_by_regex(root)
    translation.write_text(original)
    resolver = root / exporter.SWIFT_RESOLVER_FILE
    resolver.write_text(resolver.read_text().replace('"/messages"', '"/v1/messages"'))
    with pytest.raises(exporter.InventoryError, match="no longer contains"):
        exporter.macos_chat_path_rule(root)


@pytest.mark.skipif(not exporter.swiftc_available(), reason="swiftc not installed")
def test_compiled_swift_runner_agrees_with_regex_fallback():
    compiled = exporter.extract_swift_by_compile(ROOT, None)
    regex = exporter.extract_swift_by_regex(ROOT)
    assert compiled == regex
