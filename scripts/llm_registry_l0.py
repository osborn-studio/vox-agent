"""Reader for the Osborn L0 LLM registry (``osborn-llm-registry/providers@1`` + ``models@1``).

This is the ONLY module in Vox that knows the registry's field names. The schema
is published by the Osborn-PA project (``packages/llm-registry/schema/*.json``);
verbatim copies of the data, fixtures and schemas live under
``tests/fixtures/llm_registry_l0/`` (see ``PROVENANCE.md`` there).

Rules this reader implements, quoted from the providers schema description:

* consumers treat unknown ``endpoint_types`` members, unknown dialect keys and
  unknown enum values as "unsupported / ignore", never as a fatal error;
* identity bindings (``id``, ``secret.id``, ``secret.env`` order, an existing
  endpoint's ``base_url``) are immutable within ``@1``; ``compat/baseline-v1.json``
  freezes them and :func:`check_identity_bindings` verifies a document against it;
* URL rule: ``url = base_url + path`` with ``{model}`` replaced by the
  percent-encoded model id (JS ``encodeURIComponent`` semantics), string
  concatenation, never URL resolution (:func:`build_url`);
* dialect defaults when a key is absent: ``stream_options`` true,
  ``developer_role`` false, ``reasoning_split`` false, ``temperature`` true,
  ``thinking_disable`` null (no way / not needed). ``thinking_disable`` is a
  typed patch ``{"path": "a.b", "value": ...}`` written into the request body;
* secrets are never in the registry. Locations: macOS Keychain service
  ``osborn.llm`` account ``<secret.id>``; transition dual-read of
  ``com.voxlog.scalar`` / ``com.voxlog.grouped`` with account
  ``aliases.vox_keychain_account``.

A document whose ``schema`` constant is not listed in :data:`SUPPORTED_SCHEMAS`
is refused, so an outdated reader fails loudly instead of misreading a newer file.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

SUPPORTED_SCHEMAS = {
    "providers": "osborn-llm-registry/providers@1",
    "models": "osborn-llm-registry/models@1",
    "compat": "osborn-llm-registry/compat@1",
}

ENDPOINT_OPENAI_CHAT = "openai-chat-completions"
ENDPOINT_OPENAI_RESPONSES = "openai-responses"
ENDPOINT_ANTHROPIC_MESSAGES = "anthropic-messages"
ENDPOINT_GEMINI_GENERATE = "gemini-generate-content"

# Built-in copy of compat/baseline-v1.json enums; a loaded baseline overrides it.
BASELINE_ENUMS = {
    "endpoint_types": (
        ENDPOINT_OPENAI_CHAT,
        ENDPOINT_OPENAI_RESPONSES,
        ENDPOINT_ANTHROPIC_MESSAGES,
        ENDPOINT_GEMINI_GENERATE,
    ),
    "region": ("cn", "intl", "us", "local"),
    "editions": ("cn", "global"),
    "status": ("active", "deprecated"),
    "auth_type": ("api-key", "none"),
}
UNSUPPORTED = "unsupported"

NEW_KEYCHAIN_SERVICE = "osborn.llm"
LEGACY_KEYCHAIN_SERVICES = ("com.voxlog.scalar", "com.voxlog.grouped")

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_PATCH_PATH = re.compile(r"^[a-z_]+(\.[a-z_]+)*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# JS encodeURIComponent leaves these unreserved characters alone.
_ENCODE_URI_COMPONENT_SAFE = "-_.!~*'()"


class RegistrySchemaError(ValueError):
    """The registry file is missing, malformed, or declares an unsupported schema."""


@dataclass(frozen=True)
class Patch:
    """Typed request patch: write ``value`` at dot-``path`` in the JSON request body."""

    path: str
    value: Any

    def as_dict(self) -> dict:
        return {"path": self.path, "value": self.value}


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    chat_path: str
    discovery_path: str
    stream_chat_path: str | None
    thinking_disable: Patch | None
    reasoning_split: bool
    stream_options: bool
    temperature: bool
    developer_role: bool
    specified_dialect_keys: frozenset[str]
    unknown_dialect_keys: frozenset[str]
    known_type: bool
    raw: dict = field(repr=False, compare=False)

    def chat_url(self, model: str) -> str:
        return build_url(self.base_url, self.chat_path, model)

    def discovery_url(self) -> str:
        return build_url(self.base_url, self.discovery_path, None)

    def stream_chat_url(self, model: str) -> str | None:
        if self.stream_chat_path is None:
            return None
        return build_url(self.base_url, self.stream_chat_path, model)


@dataclass(frozen=True)
class Provider:
    id: str
    label_en: str
    label_zh: str
    region: str
    editions: tuple[str, ...]
    aliases: dict[str, str]
    sources: dict[str, str]
    secret_id: str
    secret_env: tuple[str, ...]
    secret_required: bool
    auth_type: str
    endpoints: dict[str, Endpoint]
    default_endpoint: str
    status: str
    raw: dict = field(repr=False, compare=False)

    @property
    def vox_alias(self) -> str | None:
        return self.aliases.get("vox")

    @property
    def vox_keychain_account(self) -> str | None:
        return self.aliases.get("vox_keychain_account")


@dataclass(frozen=True)
class Registry:
    providers: dict[str, Provider]
    models: dict[str, dict[str, dict]]
    endpoint_types: tuple[str, ...]
    providers_updated_at: str | None
    models_meta: dict

    def models_for(self, provider_id: str) -> dict[str, dict]:
        return self.models.get(provider_id, {})


# ---------------------------------------------------------------------------
# URL rule
# ---------------------------------------------------------------------------


def encode_model(model: str) -> str:
    """Percent-encode a model id with JS ``encodeURIComponent`` semantics."""

    return quote(model, safe=_ENCODE_URI_COMPONENT_SAFE)


def build_url(base_url: str, path: str, model: str | None) -> str:
    """``base_url + path`` by string concatenation, ``{model}`` percent-encoded."""

    if "{model}" in path:
        if model is None:
            raise RegistrySchemaError(f"path {path!r} needs a model id")
        path = path.replace("{model}", encode_model(model))
    return base_url + path


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_json(source: str | Path) -> Any:
    """Load JSON from a filesystem path or an ``https://`` URL."""

    text = str(source)
    if text.startswith("https://"):
        with urllib.request.urlopen(text, timeout=30) as response:  # noqa: S310 - https only
            return json.load(response)
    if text.startswith("http://"):
        raise RegistrySchemaError("registry URLs must use https")
    path = Path(text)
    if not path.exists():
        raise RegistrySchemaError(f"registry file not found: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RegistrySchemaError(f"{path} is not valid JSON: {exc}") from exc


def _check_schema(document: Any, kind: str) -> None:
    expected = SUPPORTED_SCHEMAS[kind]
    if not isinstance(document, dict):
        raise RegistrySchemaError(f"{kind} document must be a JSON object")
    declared = document.get("schema")
    if declared != expected:
        raise RegistrySchemaError(
            f"{kind} document declares schema {declared!r}; scripts/llm_registry_l0.py "
            f"understands {expected!r} only. Update the reader before reconciling."
        )


def _enum(value: Any, name: str, enums: dict[str, tuple[str, ...]]) -> str:
    """Unknown enum members become ``unsupported`` instead of raising."""

    if isinstance(value, str) and value in enums[name]:
        return value
    return UNSUPPORTED


def _parse_patch(raw: Any, where: str) -> Patch | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or "path" not in raw or "value" not in raw:
        raise RegistrySchemaError(
            f"{where}: thinking_disable must be null or a {{path, value}} patch"
        )
    path = raw["path"]
    if not isinstance(path, str) or not _PATCH_PATH.match(path):
        raise RegistrySchemaError(f"{where}: invalid patch path {path!r}")
    return Patch(path=path, value=raw["value"])


_DIALECT_DEFAULTS = {
    "stream_options": True,
    "developer_role": False,
    "reasoning_split": False,
    "temperature": True,
    "thinking_disable": None,
}


def _parse_endpoint(name: str, raw: Any, enums: dict[str, tuple[str, ...]]) -> Endpoint:
    where = f"endpoint {name!r}"
    if not isinstance(raw, dict):
        raise RegistrySchemaError(f"{where} must be an object")
    for key in ("base_url", "chat_path", "discovery_path"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise RegistrySchemaError(f"{where} needs a string {key}")
    dialect = raw.get("dialect") or {}
    if not isinstance(dialect, dict):
        raise RegistrySchemaError(f"{where} dialect must be an object")
    known = set(_DIALECT_DEFAULTS)
    specified = frozenset(key for key in dialect if key in known)
    unknown = frozenset(key for key in dialect if key not in known)

    def flag(key: str) -> bool:
        value = dialect.get(key, _DIALECT_DEFAULTS[key])
        if not isinstance(value, bool):
            raise RegistrySchemaError(f"{where} dialect.{key} must be a boolean")
        return value

    return Endpoint(
        name=name,
        base_url=raw["base_url"],
        chat_path=raw["chat_path"],
        discovery_path=raw["discovery_path"],
        stream_chat_path=raw.get("stream_chat_path"),
        thinking_disable=_parse_patch(dialect.get("thinking_disable"), where),
        reasoning_split=flag("reasoning_split"),
        stream_options=flag("stream_options"),
        temperature=flag("temperature"),
        developer_role=flag("developer_role"),
        specified_dialect_keys=specified,
        unknown_dialect_keys=unknown,
        known_type=name in enums["endpoint_types"],
        raw=raw,
    )


def _parse_provider(raw: Any, enums: dict[str, tuple[str, ...]]) -> Provider:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
        raise RegistrySchemaError("every provider needs a non-empty string id")
    provider_id = raw["id"]
    where = f"provider {provider_id!r}"
    label = raw.get("label")
    if not isinstance(label, dict) or not label.get("zh") or not label.get("en"):
        raise RegistrySchemaError(f"{where} needs label.zh and label.en")
    secret = raw.get("secret")
    if (
        not isinstance(secret, dict)
        or not isinstance(secret.get("id"), str)
        or not isinstance(secret.get("env"), list)
        or not isinstance(secret.get("required"), bool)
    ):
        raise RegistrySchemaError(f"{where} needs secret.id, secret.env and secret.required")
    auth = raw.get("auth")
    if not isinstance(auth, dict) or not isinstance(auth.get("type"), str):
        raise RegistrySchemaError(f"{where} needs auth.type")
    endpoints_raw = raw.get("endpoints")
    if not isinstance(endpoints_raw, dict) or not endpoints_raw:
        raise RegistrySchemaError(f"{where} needs at least one endpoint")
    endpoints = {name: _parse_endpoint(name, value, enums) for name, value in endpoints_raw.items()}
    default_endpoint = raw.get("default_endpoint")
    if default_endpoint not in endpoints:
        raise RegistrySchemaError(f"{where} default_endpoint {default_endpoint!r} is not declared")
    aliases_raw = raw.get("aliases") or {}
    sources_raw = raw.get("sources") or {}
    if not isinstance(aliases_raw, dict) or not isinstance(sources_raw, dict):
        raise RegistrySchemaError(f"{where} aliases and sources must be objects")
    editions = raw.get("editions") or ()
    return Provider(
        id=provider_id,
        label_en=str(label["en"]),
        label_zh=str(label["zh"]),
        region=_enum(raw.get("region"), "region", enums),
        editions=tuple(_enum(item, "editions", enums) for item in editions),
        aliases={str(k): str(v) for k, v in aliases_raw.items()},
        sources={str(k): str(v) for k, v in sources_raw.items()},
        secret_id=secret["id"],
        secret_env=tuple(str(item) for item in secret["env"]),
        secret_required=secret["required"],
        auth_type=_enum(auth["type"], "auth_type", enums),
        endpoints=endpoints,
        default_endpoint=default_endpoint,
        status=_enum(raw.get("status"), "status", enums),
        raw=raw,
    )


def parse_providers(
    document: Any, enums: dict[str, tuple[str, ...]] | None = None
) -> tuple[dict[str, Provider], tuple[str, ...], str | None]:
    _check_schema(document, "providers")
    enums = enums or BASELINE_ENUMS
    items = document.get("providers")
    if not isinstance(items, list) or not items:
        raise RegistrySchemaError("providers.json needs a non-empty 'providers' list")
    declared_types = document.get("endpoint_types") or ()
    providers: dict[str, Provider] = {}
    for raw in items:
        provider = _parse_provider(raw, enums)
        if provider.id in providers:
            raise RegistrySchemaError(f"duplicate provider id {provider.id!r}")
        providers[provider.id] = provider
    return providers, tuple(str(item) for item in declared_types), document.get("updated_at")


def parse_models(document: Any) -> tuple[dict[str, dict[str, dict]], dict]:
    _check_schema(document, "models")
    table = document.get("providers")
    if not isinstance(table, dict):
        raise RegistrySchemaError("models.json needs a 'providers' object")
    models: dict[str, dict[str, dict]] = {}
    for provider_id, catalogue in table.items():
        if not isinstance(catalogue, dict):
            raise RegistrySchemaError(f"models.json {provider_id!r} must be an object")
        models[provider_id] = {
            model_id: (spec if isinstance(spec, dict) else {})
            for model_id, spec in catalogue.items()
        }
    meta = document.get("meta")
    return models, meta if isinstance(meta, dict) else {}


def load_baseline(source: str | Path) -> dict:
    document = load_json(source)
    _check_schema(document, "compat")
    return document


def baseline_enums(baseline: dict | None) -> dict[str, tuple[str, ...]]:
    if not baseline or not isinstance(baseline.get("enums"), dict):
        return dict(BASELINE_ENUMS)
    enums = dict(BASELINE_ENUMS)
    for name, values in baseline["enums"].items():
        if name in enums and isinstance(values, list):
            enums[name] = tuple(str(item) for item in values)
    return enums


def load_registry(
    providers_source: str | Path,
    models_source: str | Path,
    baseline: dict | None = None,
) -> Registry:
    providers, endpoint_types, updated_at = parse_providers(
        load_json(providers_source), baseline_enums(baseline)
    )
    models, meta = parse_models(load_json(models_source))
    return Registry(
        providers=providers,
        models=models,
        endpoint_types=endpoint_types,
        providers_updated_at=updated_at,
        models_meta=meta,
    )


# ---------------------------------------------------------------------------
# Structural validation (jsonschema is not a Vox dev dependency)
# ---------------------------------------------------------------------------

_PROVIDER_REQUIRED = (
    "id",
    "label",
    "region",
    "editions",
    "secret",
    "auth",
    "endpoints",
    "default_endpoint",
    "status",
)
_PROVIDER_ALLOWED = set(_PROVIDER_REQUIRED) | {"aliases", "sources", "links"}
_ENDPOINT_REQUIRED = ("base_url", "chat_path", "discovery_path")
_ENDPOINT_ALLOWED = set(_ENDPOINT_REQUIRED) | {"stream_chat_path", "dialect"}
_MODEL_REQUIRED = (
    "source",
    "context",
    "output",
    "structured_output",
    "reasoning",
    "cost_usd",
    "release_date",
    "price_cny_micro",
    "response_model",
    "response_model_verified_at",
    "hint_zh",
)
_MODEL_ALLOWED = set(_MODEL_REQUIRED) | {"capabilities"}
_BASE_URL = re.compile(
    r"^(https://[^/?#@\s]+(/[^?#@\s]*[^/?#@\s])?"
    r"|http://(localhost|127\.0\.0\.1)(:\d{2,5})?(/[^?#@\s]*[^/?#@\s])?)$"
)


def validate_providers_document(document: Any) -> list[str]:
    """Minimal structural check mirroring providers.schema.json; returns problems."""

    problems: list[str] = []
    if not isinstance(document, dict):
        return ["providers document is not an object"]
    for key in ("schema", "updated_at", "endpoint_types", "providers"):
        if key not in document:
            problems.append(f"root missing {key}")
    if document.get("schema") != SUPPORTED_SCHEMAS["providers"]:
        problems.append(f"root.schema is {document.get('schema')!r}")
    if not isinstance(document.get("updated_at"), str) or not _DATE.match(document["updated_at"]):
        problems.append("root.updated_at is not YYYY-MM-DD")
    types = document.get("endpoint_types")
    if not isinstance(types, list) or not types or len(set(types)) != len(types):
        problems.append("root.endpoint_types must be a non-empty unique list")
    providers = document.get("providers")
    if not isinstance(providers, list) or not providers:
        return problems + ["root.providers must be a non-empty list"]
    seen: set[str] = set()
    for index, provider in enumerate(providers):
        where = f"providers[{index}]"
        if not isinstance(provider, dict):
            problems.append(f"{where} is not an object")
            continue
        where = f"provider {provider.get('id', index)!r}"
        for key in _PROVIDER_REQUIRED:
            if key not in provider:
                problems.append(f"{where} missing {key}")
        for key in provider:
            if key not in _PROVIDER_ALLOWED:
                problems.append(f"{where} has unexpected key {key}")
        provider_id = provider.get("id")
        if not isinstance(provider_id, str) or not _ID_PATTERN.match(provider_id):
            problems.append(f"{where} id does not match ^[a-z][a-z0-9_]{{1,31}}$")
        elif provider_id in seen:
            problems.append(f"{where} duplicate id")
        else:
            seen.add(provider_id)
        label = provider.get("label")
        if not isinstance(label, dict) or set(label) != {"zh", "en"}:
            problems.append(f"{where} label must have exactly zh and en")
        secret = provider.get("secret")
        if not isinstance(secret, dict) or set(secret) != {"id", "env", "required"}:
            problems.append(f"{where} secret must have exactly id, env, required")
        elif not isinstance(secret.get("id"), str) or not _ID_PATTERN.match(secret["id"]):
            problems.append(f"{where} secret.id is invalid")
        auth = provider.get("auth")
        if not isinstance(auth, dict) or auth.get("type") not in BASELINE_ENUMS["auth_type"]:
            problems.append(f"{where} auth.type is invalid")
        elif auth["type"] == "api-key" and set(auth) != {"type", "header", "prefix"}:
            problems.append(f"{where} api-key auth must have type, header, prefix")
        if provider.get("status") not in BASELINE_ENUMS["status"]:
            problems.append(f"{where} status is invalid")
        if provider.get("region") not in BASELINE_ENUMS["region"]:
            problems.append(f"{where} region is invalid")
        endpoints = provider.get("endpoints")
        if not isinstance(endpoints, dict) or not endpoints:
            problems.append(f"{where} endpoints must be a non-empty object")
            continue
        if provider.get("default_endpoint") not in endpoints:
            problems.append(f"{where} default_endpoint is not a declared endpoint")
        for name, endpoint in endpoints.items():
            ewhere = f"{where} endpoint {name!r}"
            if not isinstance(endpoint, dict):
                problems.append(f"{ewhere} is not an object")
                continue
            for key in _ENDPOINT_REQUIRED:
                if not isinstance(endpoint.get(key), str):
                    problems.append(f"{ewhere} missing string {key}")
            for key in endpoint:
                if key not in _ENDPOINT_ALLOWED:
                    problems.append(f"{ewhere} has unexpected key {key}")
            base_url = endpoint.get("base_url")
            if isinstance(base_url, str) and not _BASE_URL.match(base_url):
                problems.append(f"{ewhere} base_url {base_url!r} violates the schema pattern")
            for key in ("chat_path", "stream_chat_path", "discovery_path"):
                value = endpoint.get(key)
                if value is not None and (not isinstance(value, str) or not value.startswith("/")):
                    problems.append(f"{ewhere} {key} must start with /")
            dialect = endpoint.get("dialect", {})
            if not isinstance(dialect, dict):
                problems.append(f"{ewhere} dialect is not an object")
                continue
            for key, value in dialect.items():
                if key == "thinking_disable":
                    if value is not None and (
                        not isinstance(value, dict)
                        or set(value) != {"path", "value"}
                        or not isinstance(value["path"], str)
                        or not _PATCH_PATH.match(value["path"])
                    ):
                        problems.append(f"{ewhere} dialect.thinking_disable is not a patch")
                elif key in _DIALECT_DEFAULTS:
                    if not isinstance(value, bool):
                        problems.append(f"{ewhere} dialect.{key} is not a boolean")
                else:
                    problems.append(f"{ewhere} dialect has unexpected key {key}")
    return problems


def validate_models_document(document: Any) -> list[str]:
    """Minimal structural check mirroring models.schema.json; returns problems."""

    problems: list[str] = []
    if not isinstance(document, dict):
        return ["models document is not an object"]
    if set(document) != {"schema", "meta", "providers"}:
        problems.append("root must have exactly schema, meta, providers")
    if document.get("schema") != SUPPORTED_SCHEMAS["models"]:
        problems.append(f"root.schema is {document.get('schema')!r}")
    meta = document.get("meta")
    if not isinstance(meta, dict) or set(meta) != {"source", "fetched_at", "usd_cny", "generator"}:
        problems.append("root.meta must have exactly source, fetched_at, usd_cny, generator")
    elif not isinstance(meta["usd_cny"], (int, float)) or not 0 < meta["usd_cny"] < 100:
        problems.append("root.meta.usd_cny out of range")
    providers = document.get("providers")
    if not isinstance(providers, dict):
        return problems + ["root.providers must be an object"]
    for provider_id, catalogue in providers.items():
        if not _ID_PATTERN.match(provider_id):
            problems.append(f"models provider key {provider_id!r} is invalid")
        if not isinstance(catalogue, dict):
            problems.append(f"models {provider_id!r} is not an object")
            continue
        for model_id, spec in catalogue.items():
            where = f"model {provider_id}/{model_id}"
            if not re.match(r"^[A-Za-z0-9._:/-]{1,100}$", model_id):
                problems.append(f"{where} id is invalid")
            if not isinstance(spec, dict):
                problems.append(f"{where} is not an object")
                continue
            for key in _MODEL_REQUIRED:
                if key not in spec:
                    problems.append(f"{where} missing {key}")
            for key in spec:
                if key not in _MODEL_ALLOWED:
                    problems.append(f"{where} has unexpected key {key}")
            if spec.get("source") not in ("models.dev", "override"):
                problems.append(f"{where} source is invalid")
            for key in ("structured_output", "reasoning"):
                if not isinstance(spec.get(key), bool):
                    problems.append(f"{where} {key} is not a boolean")
            for key in ("context", "output"):
                value = spec.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    problems.append(f"{where} {key} is not a positive integer")
    return problems


def check_identity_bindings(document: Any, baseline: dict) -> list[str]:
    """Compare providers.json against the frozen identity table in the compat baseline."""

    problems: list[str] = []
    identity = baseline.get("identity")
    if not isinstance(identity, dict):
        return ["baseline has no identity table"]
    providers = {
        item.get("id"): item for item in document.get("providers", []) if isinstance(item, dict)
    }
    for provider_id, frozen in identity.items():
        provider = providers.get(provider_id)
        if provider is None:
            problems.append(f"{provider_id}: frozen identity but provider is gone")
            continue
        secret = provider.get("secret") or {}
        if secret.get("id") != frozen.get("secret_id"):
            problems.append(f"{provider_id}: secret.id changed to {secret.get('id')!r}")
        if list(secret.get("env") or []) != list(frozen.get("env") or []):
            problems.append(f"{provider_id}: secret.env order changed")
        endpoints = provider.get("endpoints") or {}
        for name, base_url in (frozen.get("endpoints") or {}).items():
            actual = (endpoints.get(name) or {}).get("base_url")
            if actual != base_url:
                problems.append(f"{provider_id}: endpoint {name} base_url changed to {actual!r}")
    return problems
