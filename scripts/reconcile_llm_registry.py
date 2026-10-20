"""Reconcile Vox Agent's provider inventory against the Osborn L0 LLM registry.

Reads the committed inventory snapshot (``docs/llm-registry/vox-provider-inventory.json``,
produced by ``scripts/export_llm_provider_inventory.py``) and the registry's
``providers.json`` + ``models.json`` (paths or ``https://`` URLs; defaults are the
verbatim PA copies under ``tests/fixtures/llm_registry_l0/``), applies the
``aliases.vox`` mapping with the agreed id table as fallback, and reports every
difference class without changing any runtime behaviour:

* providers in Vox but not in the registry, and the reverse;
* effective chat URL mismatches per endpoint (Vox: ``baseURL + /chat/completions``
  or ``/messages``; registry: ``base_url + chat_path``), plus informational
  base-URL-only differences and registry default endpoints that differ from the
  endpoint Vox speaks (Gemini: Vox uses the ``/v1beta/openai`` compatibility endpoint);
* models in Vox ``modelOptions`` missing from ``models.json`` and vice versa;
* dialect disagreements: Vox ``thinkingDisableField`` mapped onto the registry's
  typed ``thinking_disable`` patch, and ``reasoning_split``;
* secret id and Keychain account mapping (legacy ``tf_llm_<vox id>`` under
  ``com.voxlog.scalar`` / ``com.voxlog.grouped`` versus ``osborn.llm`` / ``<secret.id>``);
* the engine / Windows ``LlmRoute`` allowlist against the registry;
* self-checks: the registry URL rule reproduces ``fixtures/urls.json`` and the
  providers document honours the frozen identity bindings in ``compat/baseline-v1.json``.

Usage::

    .venv/bin/python scripts/reconcile_llm_registry.py                 # fixtures, Markdown
    .venv/bin/python scripts/reconcile_llm_registry.py --json          # same, JSON
    .venv/bin/python scripts/reconcile_llm_registry.py \\
        --providers https://<pa-host>/api/llm-registry/v1/providers.json \\
        --models https://<pa-host>/api/llm-registry/v1/models.json --output report.md

Exit status is 0 unless ``--strict`` is given and a difference was found (1), or
an input could not be read (2).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.llm_registry_l0 import (  # noqa: E402
    ENDPOINT_ANTHROPIC_MESSAGES,
    ENDPOINT_OPENAI_CHAT,
    LEGACY_KEYCHAIN_SERVICES,
    NEW_KEYCHAIN_SERVICE,
    Patch,
    Registry,
    RegistrySchemaError,
    check_identity_bindings,
    load_baseline,
    load_json,
    load_registry,
)

REPORT_VERSION = "2026-10-18.2"
DEFAULT_INVENTORY = ROOT / "docs/llm-registry/vox-provider-inventory.json"
FIXTURE_DIR = ROOT / "tests/fixtures/llm_registry_l0"

# Agreed on 2026-10-18 between the Vox and Osborn-PA sessions. `aliases.vox`
# in the registry is authoritative; this table is the fallback and the check.
AGREED_VOX_TO_REGISTRY = {
    "bailian": "dashscope_cn",
    "bailianUS": "dashscope_us",
    "kimi": "kimi_intl",
    "minimaxCN": "minimax_cn",
    "minimaxIntl": "minimax_intl",
    "claude": "claude",
    "ollama": "ollama",
}

# Windows / engine `LlmRoute` ids -> registry provider ids, in the order the
# inventory lists that route's base URLs (qwen-turbo: CN first, then US).
WINDOWS_ROUTE_TO_REGISTRY: dict[str, tuple[str, ...]] = {
    "openai-gpt": ("openai",),
    "qwen-turbo": ("dashscope_cn", "dashscope_us"),
    "doubao": ("doubao",),
    "claude": ("claude",),
    "openai-compatible-custom": (),
    "ollama": ("ollama",),
}

# Vox `LLMProvider.thinkingDisableField` -> the registry's typed request patch.
# Each line mirrors the comment on the Swift enum case.
THINKING_FIELD_TO_PATCH: dict[str | None, Patch | None] = {
    "thinking": Patch("thinking.type", "disabled"),
    "enable_thinking": Patch("enable_thinking", False),
    "reasoning_effort": Patch("reasoning_effort", "none"),
    "think": Patch("think", False),
    None: None,
}

ENDPOINT_NOTES = {
    "gemini": (
        "Vox speaks the OpenAI-compatible endpoint at /v1beta/openai; the registry's "
        "default is the native generateContent endpoint. Not a defect while Vox has no "
        "Gemini-native client."
    ),
}

INFORMATIONAL_CLASSES = frozenset({"default_endpoint_differences", "base_url_only_differences"})


class ReconcileError(RuntimeError):
    """Raised when an input cannot be read."""


def agreed_registry_id(vox_id: str) -> str:
    return AGREED_VOX_TO_REGISTRY.get(vox_id, vox_id)


def vox_endpoint_name(provider: dict) -> str:
    return ENDPOINT_OPENAI_CHAT if provider["is_openai_compatible"] else ENDPOINT_ANTHROPIC_MESSAGES


def vox_chat_url(inventory: dict, base_url: str, openai_compatible: bool) -> str:
    """Vox's effective request URL: trailing slashes trimmed, fixed path appended."""

    rule = inventory["macos"]["chat_path_rule"]
    path = rule["openai_compatible"] if openai_compatible else rule["anthropic"]
    return base_url.rstrip("/") + path


def runtime_chat_url(inventory: dict, base_url: str, protocol: str) -> str:
    rule = inventory["engine_windows"]["chat_path_rule"]
    return base_url.rstrip("/") + rule[protocol]


def vox_thinking_patch(field_name: str | None) -> dict | None:
    patch = THINKING_FIELD_TO_PATCH[field_name]
    return None if patch is None else patch.as_dict()


def has_differences(summary: dict[str, int]) -> bool:
    """True when any non-informational difference class is non-zero."""

    return any(count for key, count in summary.items() if key not in INFORMATIONAL_CLASSES)


def load_inventory(path: Path) -> dict:
    if not path.exists():
        raise ReconcileError(f"inventory not found: {path}")
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def map_providers(inventory: dict, registry: Registry) -> list[dict]:
    """One row per Vox provider: which registry id it maps to and how."""

    alias_index: dict[str, str] = {}
    for provider in registry.providers.values():
        if provider.vox_alias:
            if provider.vox_alias in alias_index:
                raise ReconcileError(
                    f"registry aliases.vox {provider.vox_alias!r} is claimed by both "
                    f"{alias_index[provider.vox_alias]!r} and {provider.id!r}"
                )
            alias_index[provider.vox_alias] = provider.id
    rows = []
    for provider in inventory["macos"]["providers"]:
        vox_id = provider["id"]
        agreed = agreed_registry_id(vox_id)
        if vox_id in alias_index:
            registry_id: str | None = alias_index[vox_id]
            source = "aliases.vox"
        elif agreed in registry.providers:
            registry_id = agreed
            source = "agreed-table" if vox_id in AGREED_VOX_TO_REGISTRY else "same-name"
        else:
            registry_id = None
            source = "unmapped"
        rows.append(
            {
                "vox_id": vox_id,
                "registry_id": registry_id,
                "agreed_id": agreed,
                "source": source,
                "disagrees_with_agreed_table": registry_id is not None and registry_id != agreed,
                "registry_status": registry.providers[registry_id].status if registry_id else None,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Difference classes
# ---------------------------------------------------------------------------


def reconcile(
    inventory: dict,
    registry: Registry,
    *,
    urls_fixture: list[dict] | None = None,
    baseline: dict | None = None,
    providers_document: dict | None = None,
    inputs: dict | None = None,
) -> dict:
    mapping = map_providers(inventory, registry)
    by_vox = {provider["id"]: provider for provider in inventory["macos"]["providers"]}
    mapped = {row["vox_id"]: row["registry_id"] for row in mapping if row["registry_id"]}
    vox_only = sorted(row["vox_id"] for row in mapping if row["registry_id"] is None)
    registry_only = sorted(set(registry.providers) - set(mapped.values()))
    status_warnings = [
        {
            "vox_id": row["vox_id"],
            "registry_id": row["registry_id"],
            "status": row["registry_status"],
        }
        for row in mapping
        if row["registry_id"] and row["registry_status"] != "active"
    ]

    chat_url_mismatches: list[dict] = []
    base_url_only_differences: list[dict] = []
    default_endpoint_differences: list[dict] = []
    models_missing_in_registry: dict[str, list[str]] = {}
    models_missing_in_vox: dict[str, list[str]] = {}
    models_not_compared: list[dict] = []
    dialect_disagreements: list[dict] = []
    dialect_notes: list[dict] = []
    secrets: list[dict] = []
    legacy_accounts = inventory["macos"]["keychain"]["accounts"]
    legacy_services = list(inventory["macos"]["keychain"]["services"])

    for vox_id, registry_id in sorted(mapped.items()):
        vox = by_vox[vox_id]
        reg = registry.providers[registry_id]
        endpoint_name = vox_endpoint_name(vox)
        endpoint = reg.endpoints.get(endpoint_name)
        vox_url = vox_chat_url(inventory, vox["default_base_url"], vox["is_openai_compatible"])

        # Effective chat URL per endpoint.
        if endpoint is None:
            chat_url_mismatches.append(
                {
                    "vox_id": vox_id,
                    "registry_id": registry_id,
                    "endpoint": endpoint_name,
                    "vox": vox_url,
                    "registry": None,
                    "reason": "endpoint not declared in registry",
                }
            )
        else:
            registry_url = endpoint.chat_url(_sample_model(vox, registry, registry_id))
            if registry_url != vox_url:
                chat_url_mismatches.append(
                    {
                        "vox_id": vox_id,
                        "registry_id": registry_id,
                        "endpoint": endpoint_name,
                        "vox": vox_url,
                        "registry": registry_url,
                        "reason": "effective chat URL differs",
                    }
                )
            elif endpoint.base_url.rstrip("/") != vox["default_base_url"].rstrip("/"):
                base_url_only_differences.append(
                    {
                        "vox_id": vox_id,
                        "registry_id": registry_id,
                        "endpoint": endpoint_name,
                        "vox_base_url": vox["default_base_url"],
                        "registry_base_url": endpoint.base_url,
                        "registry_chat_path": endpoint.chat_path,
                        "effective_url": vox_url,
                    }
                )
        if reg.default_endpoint != endpoint_name:
            default_endpoint_differences.append(
                {
                    "vox_id": vox_id,
                    "registry_id": registry_id,
                    "vox_endpoint": endpoint_name,
                    "registry_default_endpoint": reg.default_endpoint,
                    "note": ENDPOINT_NOTES.get(vox_id),
                }
            )

        # Models.
        vox_models = [option["value"] for option in vox["model_options"]]
        if registry_id not in registry.models:
            if vox_models:
                models_missing_in_registry[vox_id] = sorted(vox_models)
            models_not_compared.append(
                {"vox_id": vox_id, "registry_id": registry_id, "reason": "no models.json entry"}
            )
        else:
            registry_models = set(registry.models_for(registry_id))
            missing_in_registry = sorted(set(vox_models) - registry_models)
            if missing_in_registry:
                models_missing_in_registry[vox_id] = missing_in_registry
            if vox_models:
                missing_in_vox = sorted(registry_models - set(vox_models))
                if missing_in_vox:
                    models_missing_in_vox[vox_id] = missing_in_vox
            elif vox["translation"]["accepts_any_model"]:
                models_not_compared.append(
                    {
                        "vox_id": vox_id,
                        "registry_id": registry_id,
                        "reason": "Vox lists no modelOptions and accepts any model id",
                    }
                )

        # Dialect.
        if endpoint is not None:
            vox_patch = vox_thinking_patch(vox["thinking_disable_field"])
            registry_patch = (
                None if endpoint.thinking_disable is None else endpoint.thinking_disable.as_dict()
            )
            comparisons = (
                ("thinking_disable", vox_patch, registry_patch),
                ("reasoning_split", vox["needs_reasoning_split"], endpoint.reasoning_split),
            )
            for field_name, vox_value, registry_value in comparisons:
                if registry_value != vox_value:
                    dialect_disagreements.append(
                        {
                            "vox_id": vox_id,
                            "registry_id": registry_id,
                            "endpoint": endpoint_name,
                            "field": field_name,
                            "vox": vox_value,
                            "registry": registry_value,
                            "registry_specified": field_name in endpoint.specified_dialect_keys,
                        }
                    )
            if endpoint.unknown_dialect_keys:
                dialect_notes.append(
                    {
                        "vox_id": vox_id,
                        "registry_id": registry_id,
                        "endpoint": endpoint_name,
                        "ignored_dialect_keys": sorted(endpoint.unknown_dialect_keys),
                    }
                )

        # Secrets / Keychain.
        legacy_account = legacy_accounts[vox_id]
        secrets.append(
            {
                "vox_id": vox_id,
                "registry_id": registry_id,
                "secret_id": reg.secret_id,
                "secret_env": list(reg.secret_env),
                "secret_required": reg.secret_required,
                "auth_type": reg.auth_type,
                "legacy_services": legacy_services,
                "legacy_account": legacy_account,
                "new_service": NEW_KEYCHAIN_SERVICE,
                "new_account": reg.secret_id,
                "registry_vox_keychain_account": reg.vox_keychain_account,
                "keychain_alias_matches": reg.vox_keychain_account == legacy_account,
                "requires_api_key": vox["requires_api_key"],
                "auth_requirement_matches": reg.secret_required == vox["requires_api_key"],
            }
        )

    windows_routes = _windows_routes(inventory, registry)
    url_rule_check = _url_rule_check(registry, urls_fixture)
    identity_check = (
        check_identity_bindings(providers_document, baseline)
        if baseline is not None and providers_document is not None
        else None
    )

    summary = {
        "providers_vox_only": len(vox_only),
        "providers_registry_only": len(registry_only),
        "mapping_disagreements": sum(1 for row in mapping if row["disagrees_with_agreed_table"]),
        "status_warnings": len(status_warnings),
        "chat_url_mismatches": len(chat_url_mismatches),
        "base_url_only_differences": len(base_url_only_differences),
        "default_endpoint_differences": len(default_endpoint_differences),
        "models_missing_in_registry": sum(len(v) for v in models_missing_in_registry.values()),
        "models_missing_in_vox": sum(len(v) for v in models_missing_in_vox.values()),
        "dialect_disagreements": len(dialect_disagreements),
        "keychain_alias_mismatches": sum(1 for row in secrets if not row["keychain_alias_matches"]),
        "auth_requirement_mismatches": sum(
            1 for row in secrets if not row["auth_requirement_matches"]
        ),
        "windows_route_issues": sum(1 for row in windows_routes if row["issues"]),
        "url_rule_failures": len(url_rule_check["failures"]) if url_rule_check else 0,
        "identity_binding_violations": len(identity_check) if identity_check else 0,
    }
    return {
        "report_version": REPORT_VERSION,
        "inputs": inputs or {},
        "registry": {
            "providers_schema": "osborn-llm-registry/providers@1",
            "models_schema": "osborn-llm-registry/models@1",
            "providers_updated_at": registry.providers_updated_at,
            "endpoint_types": list(registry.endpoint_types),
            "models_meta": registry.models_meta,
            "provider_count": len(registry.providers),
            "model_count": sum(len(models) for models in registry.models.values()),
        },
        "id_mapping": mapping,
        "providers_vox_only": vox_only,
        "providers_registry_only": registry_only,
        "status_warnings": status_warnings,
        "chat_url_mismatches": chat_url_mismatches,
        "base_url_only_differences": base_url_only_differences,
        "default_endpoint_differences": default_endpoint_differences,
        "models_missing_in_registry": models_missing_in_registry,
        "models_missing_in_vox": models_missing_in_vox,
        "models_not_compared": models_not_compared,
        "dialect_disagreements": dialect_disagreements,
        "dialect_notes": dialect_notes,
        "secrets": secrets,
        "windows_routes": windows_routes,
        "url_rule_check": url_rule_check,
        "identity_check": identity_check,
        "summary": summary,
        "has_differences": has_differences(summary),
    }


def _sample_model(vox: dict, registry: Registry, registry_id: str) -> str:
    options = [option["value"] for option in vox["model_options"]]
    if options:
        return options[0]
    models = sorted(registry.models_for(registry_id))
    return models[0] if models else "model"


def _windows_routes(inventory: dict, registry: Registry) -> list[dict]:
    rows = []
    for route in inventory["engine_windows"]["routes"]:
        route_id = route["id"]
        if route_id not in WINDOWS_ROUTE_TO_REGISTRY:
            rows.append(
                {
                    "route_id": route_id,
                    "protocol": route["protocol"],
                    "registry_ids": [],
                    "base_urls": route["base_urls"],
                    "user_configured_endpoint": route.get("user_configured_endpoint", False),
                    "issues": [
                        "route has no registry mapping in scripts/reconcile_llm_registry.py"
                    ],
                }
            )
            continue
        targets = WINDOWS_ROUTE_TO_REGISTRY[route_id]
        issues: list[str] = []
        endpoint_name = (
            ENDPOINT_ANTHROPIC_MESSAGES
            if route["protocol"] == "anthropic"
            else ENDPOINT_OPENAI_CHAT
        )
        for index, registry_id in enumerate(targets):
            reg = registry.providers.get(registry_id)
            if reg is None:
                issues.append(f"{registry_id} missing from registry")
                continue
            if reg.status != "active":
                issues.append(f"{registry_id} status is {reg.status}")
            endpoint = reg.endpoints.get(endpoint_name)
            if endpoint is None:
                issues.append(f"{registry_id} declares no {endpoint_name} endpoint")
                continue
            if index < len(route["base_urls"]):
                runtime_url = runtime_chat_url(
                    inventory, route["base_urls"][index], route["protocol"]
                )
                registry_url = endpoint.chat_url(route["default_model"] or "model")
                if registry_url != runtime_url:
                    issues.append(
                        f"{registry_id} {endpoint_name} chat URL {registry_url} != "
                        f"runtime {runtime_url}"
                    )
        rows.append(
            {
                "route_id": route_id,
                "protocol": route["protocol"],
                "registry_ids": list(targets),
                "base_urls": route["base_urls"],
                "user_configured_endpoint": route.get("user_configured_endpoint", False),
                "issues": issues,
            }
        )
    return rows


def _url_rule_check(registry: Registry, urls_fixture: list[dict] | None) -> dict | None:
    """Reproduce every row of PA's fixtures/urls.json with the reader's URL rule."""

    if urls_fixture is None:
        return None
    failures: list[dict] = []
    checked = 0
    for row in urls_fixture:
        provider = registry.providers.get(row.get("provider"))
        endpoint = provider.endpoints.get(row.get("endpoint")) if provider else None
        if endpoint is None:
            failures.append({**row, "computed": None, "reason": "provider/endpoint not found"})
            continue
        which = row.get("which")
        model = row.get("model")
        try:
            if which == "chat":
                computed: str | None = endpoint.chat_url(model)
            elif which == "stream_chat":
                computed = endpoint.stream_chat_url(model)
            elif which == "discovery":
                computed = endpoint.discovery_url()
            else:
                failures.append({**row, "computed": None, "reason": f"unknown which {which!r}"})
                continue
        except RegistrySchemaError as exc:
            failures.append({**row, "computed": None, "reason": str(exc)})
            continue
        checked += 1
        if computed != row.get("url"):
            failures.append({**row, "computed": computed, "reason": "URL differs"})
    return {"checked": checked, "failures": failures}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _code(value: Any) -> str:
    if value is None:
        return "`null`"
    if isinstance(value, bool):
        return "`true`" if value else "`false`"
    if isinstance(value, dict):
        return "`" + json.dumps(value, ensure_ascii=False, sort_keys=True) + "`"
    return f"`{value}`"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def _section(out: list[str], title: str) -> None:
    out.append(f"## {title}")
    out.append("")


def _bullets_or_none(out: list[str], items: list[str]) -> None:
    out.extend(items or ["- none"])
    out.append("")


def render_markdown(report: dict) -> str:
    out: list[str] = ["# Vox Agent ↔ L0 LLM registry reconciliation", ""]
    inputs = report.get("inputs", {})
    if inputs:
        out.append("Inputs:")
        out.append("")
        out.extend(f"- {key}: `{value}`" for key, value in inputs.items())
        out.append("")
    reg = report["registry"]
    out.append(
        f"Registry: `{reg['providers_schema']}` updated {_code(reg['providers_updated_at'])}, "
        f"{reg['provider_count']} providers; `{reg['models_schema']}` with {reg['model_count']} "
        f"models (models.dev fetched {_code(reg['models_meta'].get('fetched_at'))}). "
        f"Report version `{report['report_version']}`."
    )
    out.append("")

    _section(out, "Summary")
    out.extend(
        _table(
            ["Difference class", "Count", "Counts toward --strict"],
            [
                [key.replace("_", " "), count, "no" if key in INFORMATIONAL_CLASSES else "yes"]
                for key, count in report["summary"].items()
            ],
        )
    )
    out.append("")

    _section(out, "Provider id mapping")
    out.extend(
        _table(
            ["Vox id", "Registry id", "Source", "Agreed id", "Disagrees", "Registry status"],
            [
                [
                    _code(row["vox_id"]),
                    _code(row["registry_id"]),
                    row["source"],
                    _code(row["agreed_id"]),
                    "**yes**" if row["disagrees_with_agreed_table"] else "no",
                    _code(row["registry_status"]),
                ]
                for row in report["id_mapping"]
            ],
        )
    )
    out.append("")

    _section(out, "Providers in Vox but not in the registry")
    _bullets_or_none(out, [f"- {_code(item)}" for item in report["providers_vox_only"]])

    _section(out, "Providers in the registry but not in Vox")
    _bullets_or_none(out, [f"- {_code(item)}" for item in report["providers_registry_only"]])

    if report["status_warnings"]:
        _section(out, "Mapped providers whose registry status is not active")
        _bullets_or_none(
            out,
            [
                f"- {_code(row['vox_id'])} → {_code(row['registry_id'])}: {_code(row['status'])}"
                for row in report["status_warnings"]
            ],
        )

    _section(out, "Effective chat URL mismatches")
    out.append(
        "Vox: `baseURL` with trailing slashes trimmed plus `/chat/completions` "
        "(`/messages` for Claude). Registry: `base_url + chat_path`."
    )
    out.append("")
    if report["chat_url_mismatches"]:
        out.extend(
            _table(
                ["Vox id", "Registry id", "Endpoint", "Vox", "Registry", "Reason"],
                [
                    [
                        _code(r["vox_id"]),
                        _code(r["registry_id"]),
                        _code(r["endpoint"]),
                        _code(r["vox"]),
                        _code(r["registry"]),
                        r["reason"],
                    ]
                    for r in report["chat_url_mismatches"]
                ],
            )
        )
    else:
        out.append("- none")
    out.append("")
    if report["base_url_only_differences"]:
        out.append("### Base URL split differently, same effective URL (informational)")
        out.append("")
        out.extend(
            _table(
                [
                    "Vox id",
                    "Registry id",
                    "Vox baseURL",
                    "Registry base_url",
                    "chat_path",
                    "Effective",
                ],
                [
                    [
                        _code(r["vox_id"]),
                        _code(r["registry_id"]),
                        _code(r["vox_base_url"]),
                        _code(r["registry_base_url"]),
                        _code(r["registry_chat_path"]),
                        _code(r["effective_url"]),
                    ]
                    for r in report["base_url_only_differences"]
                ],
            )
        )
        out.append("")
    if report["default_endpoint_differences"]:
        out.append(
            "### Registry default endpoint differs from the endpoint Vox speaks (informational)"
        )
        out.append("")
        for row in report["default_endpoint_differences"]:
            line = (
                f"- {_code(row['vox_id'])} → {_code(row['registry_id'])}: Vox uses "
                f"{_code(row['vox_endpoint'])}, registry default is "
                f"{_code(row['registry_default_endpoint'])}."
            )
            if row.get("note"):
                line += f" {row['note']}"
            out.append(line)
        out.append("")

    _section(out, "Models in Vox `modelOptions` missing from `models.json`")
    _bullets_or_none(
        out,
        [
            f"- {_code(vox_id)}: " + ", ".join(_code(m) for m in models)
            for vox_id, models in report["models_missing_in_registry"].items()
        ],
    )

    _section(out, "Models in `models.json` missing from Vox `modelOptions`")
    _bullets_or_none(
        out,
        [
            f"- {_code(vox_id)} ({len(models)}): " + ", ".join(_code(m) for m in models)
            for vox_id, models in report["models_missing_in_vox"].items()
        ],
    )
    if report["models_not_compared"]:
        out.append("Not compared:")
        out.append("")
        _bullets_or_none(
            out,
            [
                f"- {_code(r['vox_id'])} → {_code(r['registry_id'])}: {r['reason']}"
                for r in report["models_not_compared"]
            ],
        )

    _section(out, "Dialect disagreements (`thinking_disable` patch, `reasoning_split`)")
    out.append(
        "Vox `thinkingDisableField` is mapped onto the registry's typed patch: "
        + ", ".join(
            f"`{name}` → {_code(patch.as_dict() if patch else None)}"
            for name, patch in THINKING_FIELD_TO_PATCH.items()
            if name is not None
        )
        + ". Absent registry keys take the schema defaults."
    )
    out.append("")
    if report["dialect_disagreements"]:
        out.extend(
            _table(
                ["Vox id", "Registry id", "Endpoint", "Field", "Vox", "Registry", "Specified"],
                [
                    [
                        _code(r["vox_id"]),
                        _code(r["registry_id"]),
                        _code(r["endpoint"]),
                        _code(r["field"]),
                        _code(r["vox"]),
                        _code(r["registry"]),
                        "yes" if r["registry_specified"] else "default",
                    ]
                    for r in report["dialect_disagreements"]
                ],
            )
        )
    else:
        out.append("- none")
    out.append("")
    if report["dialect_notes"]:
        out.append("Dialect keys this reader ignores (unknown, treated as unsupported):")
        out.append("")
        _bullets_or_none(
            out,
            [
                f"- {_code(r['vox_id'])} {_code(r['endpoint'])}: "
                + ", ".join(_code(k) for k in r["ignored_dialect_keys"])
                for r in report["dialect_notes"]
            ],
        )

    _section(out, "Secret id and Keychain account mapping")
    out.append(
        "Legacy location: services "
        + ", ".join(_code(s) for s in LEGACY_KEYCHAIN_SERVICES)
        + f", account `tf_llm_<vox id>`; new location: service {_code(NEW_KEYCHAIN_SERVICE)}, "
        "account `<secret.id>`. Vox dual-reads both during the transition."
    )
    out.append("")
    out.extend(
        _table(
            [
                "Vox id",
                "Registry id",
                "secret.id",
                "secret.env",
                "Legacy account",
                "Alias matches",
                "Key required (Vox / registry)",
            ],
            [
                [
                    _code(r["vox_id"]),
                    _code(r["registry_id"]),
                    _code(r["secret_id"]),
                    ", ".join(_code(e) for e in r["secret_env"]) or "—",
                    _code(r["legacy_account"]),
                    "yes" if r["keychain_alias_matches"] else "**no**",
                    f"{_code(r['requires_api_key'])} / {_code(r['secret_required'])}"
                    + ("" if r["auth_requirement_matches"] else " **≠**"),
                ]
                for r in report["secrets"]
            ],
        )
    )
    out.append("")

    _section(out, "Engine / Windows `LlmRoute` allowlist")
    out.extend(
        _table(
            ["Route", "Protocol", "Registry ids", "Issues"],
            [
                [
                    _code(r["route_id"]),
                    _code(r.get("protocol")),
                    ", ".join(_code(i) for i in r["registry_ids"]) or "user-configured",
                    "; ".join(r["issues"]) or "none",
                ]
                for r in report["windows_routes"]
            ],
        )
    )
    out.append("")

    _section(out, "Registry self-checks")
    check = report["url_rule_check"]
    if check is None:
        out.append("- URL rule: no `urls.json` fixture given.")
    else:
        out.append(
            f"- URL rule (`base_url + path`, `{{model}}` percent-encoded): "
            f"{check['checked']} fixture URLs reproduced, {len(check['failures'])} failures."
        )
        for failure in check["failures"]:
            out.append(
                f"  - {failure.get('provider')} {failure.get('endpoint')} {failure.get('which')}: "
                f"{failure['reason']} (fixture {_code(failure.get('url'))}, "
                f"computed {_code(failure.get('computed'))})"
            )
    identity = report["identity_check"]
    if identity is None:
        out.append("- Identity bindings: no compat baseline given.")
    elif identity:
        out.append(
            f"- Identity bindings: **{len(identity)} violation(s)** against "
            "`compat/baseline-v1.json`:"
        )
        out.extend(f"  - {item}" for item in identity)
    else:
        out.append(
            "- Identity bindings: providers.json honours every frozen binding in "
            "`compat/baseline-v1.json`."
        )
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(
    inventory_path: Path,
    providers_source: str,
    models_source: str,
    urls_source: str | None = None,
    baseline_source: str | None = None,
) -> dict:
    inventory = load_inventory(inventory_path)
    try:
        baseline = load_baseline(baseline_source) if baseline_source else None
        registry = load_registry(providers_source, models_source, baseline)
        providers_document = load_json(providers_source) if baseline is not None else None
        urls_fixture = load_json(urls_source) if urls_source else None
    except RegistrySchemaError as exc:
        raise ReconcileError(str(exc)) from exc
    if urls_fixture is not None and not isinstance(urls_fixture, list):
        raise ReconcileError("urls.json must be a list of rows")
    inputs = {
        "inventory": str(inventory_path),
        "providers": str(providers_source),
        "models": str(models_source),
    }
    if urls_source:
        inputs["urls"] = str(urls_source)
    if baseline_source:
        inputs["baseline"] = str(baseline_source)
    return reconcile(
        inventory,
        registry,
        urls_fixture=urls_fixture,
        baseline=baseline,
        providers_document=providers_document,
        inputs=inputs,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument(
        "--providers",
        default=str(FIXTURE_DIR / "providers.json"),
        help="providers.json path or https URL (default: PA copy under tests/fixtures)",
    )
    parser.add_argument(
        "--models",
        default=str(FIXTURE_DIR / "models.json"),
        help="models.json path or https URL (default: PA copy under tests/fixtures)",
    )
    parser.add_argument(
        "--urls",
        default=str(FIXTURE_DIR / "urls.json"),
        help="PA fixtures/urls.json for the URL-rule self-check; 'none' to skip",
    )
    parser.add_argument(
        "--baseline",
        default=str(FIXTURE_DIR / "baseline-v1.json"),
        help="PA compat/baseline-v1.json for enums and identity bindings; 'none' to skip",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    parser.add_argument("--output", type=Path, help="write the report here instead of stdout")
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 when any difference is reported"
    )
    args = parser.parse_args(argv)
    urls = None if args.urls == "none" else args.urls
    baseline = None if args.baseline == "none" else args.baseline
    try:
        report = run(args.inventory, args.providers, args.models, urls, baseline)
    except ReconcileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    text = (
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.json
        else render_markdown(report)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    if args.strict and report["has_differences"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
