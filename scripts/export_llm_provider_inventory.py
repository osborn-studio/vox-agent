"""Export Vox Agent's current LLM provider truth as deterministic JSON.

Two surfaces are inventoried without changing runtime behaviour:

* the native macOS enum ``LLMProvider`` (``macos/VoxLog/LLM/LLMProvider.swift``)
  together with the audited ``TranslationProtocol.supports`` table
  (``macos/VoxLog/Translation/TranslationTransport.swift``);
* the engine / Windows route allowlist: the Rust ``LlmRoute`` enum, the shared
  Zod ``llmRouteSchema``, the Python ``resolve_llm_provider`` resolver and the
  ``PROVIDERS`` qualification inventory in ``runtime/``.

The Swift surface is extracted by compiling a tiny ``swiftc`` runner against
the real sources (plus stubs for UI-only symbols) and running it. When no
``swiftc`` is available (Linux CI) the exporter falls back to a documented
regex parse of the same two files; the two paths must agree, and
``tests/test_llm_registry_reconcile.py`` asserts that wherever ``swiftc`` runs.

The committed snapshot lives at ``docs/llm-registry/vox-provider-inventory.json``.
``--check`` fails when the snapshot no longer matches the sources.

Usage::

    .venv/bin/python scripts/export_llm_provider_inventory.py            # rewrite snapshot
    .venv/bin/python scripts/export_llm_provider_inventory.py --check    # CI staleness gate
    .venv/bin/python scripts/export_llm_provider_inventory.py --swift-mode regex --stdout
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INVENTORY_VERSION = "2026-10-18.2"
DEFAULT_SNAPSHOT = ROOT / "docs/llm-registry/vox-provider-inventory.json"

SWIFT_PROVIDER_FILE = "macos/VoxLog/LLM/LLMProvider.swift"
SWIFT_TRANSLATION_FILE = "macos/VoxLog/Translation/TranslationTransport.swift"
SWIFT_KEYCHAIN_FILE = "macos/VoxLog/Services/KeychainService.swift"
SWIFT_RESOLVER_FILE = "macos/VoxLog/Translation/SelectedLLMConfigurationResolver.swift"
RUST_CONTRACTS_FILE = "apps/desktop-tauri/src-tauri/src/contracts.rs"
TS_SETTINGS_FILE = "packages/contracts/src/settings.ts"
PY_RESOLVER_FILE = "runtime/llm_provider.py"
PY_QUALIFICATION_FILE = "runtime/provider_qualification.py"

# Swift `ThinkingDisableField` case -> registry dialect vocabulary.
THINKING_FIELD_VOCAB = {
    "thinking": "thinking",
    "enableThinking": "enable_thinking",
    "reasoningEffort": "reasoning_effort",
    "think": "think",
}

# Model ids `TranslationProtocol.supports` accepts beyond `modelOptions`. The
# compiled runner probes these against every provider so hard-coded aliases in
# the Swift table surface in the inventory without the exporter guessing.
TRANSLATION_PROBE_EXTRAS = (
    "qwen3.8-omni-flash",
    "deepseek-flash",
    "deepseek-v4-pro",
    "gpt-4o",
)
TRANSLATION_ANY_MODEL_PROBE = "__vox_inventory_probe__"

# Keychain locations Vox writes today (KeychainService.swift). The exporter
# verifies the literal patterns still exist in the source before emitting them.
KEYCHAIN_SERVICE_PREFIX = "com.voxlog"
KEYCHAIN_ACCOUNT_PATTERN = "tf_llm_<provider id>"


class InventoryError(RuntimeError):
    """Raised when a source cannot be parsed or a check fails."""


# ---------------------------------------------------------------------------
# Swift surface: compiled runner
# ---------------------------------------------------------------------------

SWIFT_STUBS = """import Foundation
func L(_ zh: String, _ en: String) -> String { en }
struct FieldOption: Sendable { let value: String; let label: String }
struct CredentialField: Sendable {}
struct LLMConfig: Sendable {}
enum TranslationTarget: String, Sendable { case simplifiedChinese
    var instruction: String { "inventory stub" } }
enum TranslationFailure: Error, Equatable {
    case configuration, unsupported, denied, busy, inputLimit, outputLimit, incomplete
    case syntax, protection, empty, protocolError, network, timeout, cancelled
}
struct TranslationConfiguration: Sendable {
    let provider: LLMProvider
    let endpoint: URL
    let model: String
    let credential: String
    let bypassProxy: Bool
}
"""

SWIFT_RUNNER = """import Foundation

@main
struct InventoryRunner {
    static func thinkingField(_ field: ThinkingDisableField?) -> Any {
        switch field {
        case .none: return NSNull()
        case .some(.thinking): return "thinking"
        case .some(.enableThinking): return "enable_thinking"
        case .some(.reasoningEffort): return "reasoning_effort"
        case .some(.think): return "think"
        }
    }

    static func main() throws {
        let extras = CommandLine.arguments.dropFirst()
        var probes = Set<String>(extras)
        for provider in LLMProvider.allCases {
            for option in provider.modelOptions { probes.insert(option.value) }
        }
        var providers: [[String: Any]] = []
        for provider in LLMProvider.allCases {
            let listed = provider.modelOptions.map { $0.value }
            var supported = probes.filter { TranslationProtocol.supports(provider, model: $0) }
            let anyModel = TranslationProtocol.supports(provider, model: "__vox_inventory_probe__")
            if anyModel { supported = [] }
            providers.append([
                "id": provider.rawValue,
                "display_name": provider.displayName,
                "default_base_url": provider.defaultBaseURL,
                "model_options": provider.modelOptions.map { ["value": $0.value, "label": $0.label] },
                "thinking_disable_field": thinkingField(provider.thinkingDisableField),
                "needs_reasoning_split": provider.needsReasoningSplit,
                "requires_api_key": provider.requiresAPIKey,
                "is_openai_compatible": provider.isOpenAICompatible,
                "is_local": provider.isLocal,
                "translation": [
                    "accepts_any_model": anyModel,
                    "supported_models": Array(supported).sorted(),
                    "supported_beyond_model_options": Array(supported.subtracting(listed)).sorted(),
                ],
            ])
        }
        let data = try JSONSerialization.data(
            withJSONObject: ["providers": providers], options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
    }
}
"""


def swiftc_available() -> bool:
    return shutil.which("swiftc") is not None


def compile_available() -> str | None:
    """Why the compiled Swift path cannot run here, or None when it can.

    GitHub's ubuntu runners ship ``swiftc`` but no ``xcrun``/SDK, so the gate
    needs all three: macOS, a compiler, and a resolvable SDK path.
    """

    if sys.platform != "darwin":
        return f"compiled Swift extraction needs macOS (platform is {sys.platform})"
    if not swiftc_available():
        return "swiftc is not installed"
    if default_sdk_path() is None:
        return "xcrun --show-sdk-path failed; install Xcode or the Command Line Tools"
    return None


def default_sdk_path() -> str | None:
    xcrun = shutil.which("xcrun")
    if xcrun is None:
        return None
    try:
        result = subprocess.run(
            [xcrun, "--show-sdk-path"], check=True, capture_output=True, text=True, timeout=30
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    path = result.stdout.strip()
    return path or None


def extract_swift_by_compile(root: Path, sdk: str | None, work_dir: Path | None = None) -> dict:
    """Compile and run the Swift runner; returns the ``providers`` list payload."""

    if not swiftc_available():
        raise InventoryError("swiftc is not available; use --swift-mode regex")
    sdk = sdk or default_sdk_path()
    if sdk is None:
        raise InventoryError("no SDK path; pass --sdk or install Command Line Tools")
    own_tmp = work_dir is None
    work = Path(tempfile.mkdtemp(prefix="vox-llm-inventory-")) if own_tmp else work_dir
    assert work is not None
    work.mkdir(parents=True, exist_ok=True)
    try:
        (work / "stubs.swift").write_text(SWIFT_STUBS)
        (work / "InventoryRunner.swift").write_text(SWIFT_RUNNER)
        exe = work / "inventory-runner"
        command = [
            "swiftc",
            "-sdk",
            sdk,
            "-parse-as-library",
            "-module-cache-path",
            str(work / "module-cache"),
            str(root / SWIFT_PROVIDER_FILE),
            str(root / SWIFT_TRANSLATION_FILE),
            str(work / "stubs.swift"),
            str(work / "InventoryRunner.swift"),
            "-o",
            str(exe),
        ]
        compile_result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if compile_result.returncode != 0:
            raise InventoryError(
                "swiftc failed to build the inventory runner:\n" + compile_result.stderr[-4000:]
            )
        run_result = subprocess.run(
            [str(exe), *TRANSLATION_PROBE_EXTRAS],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        payload = json.loads(run_result.stdout)
    finally:
        if own_tmp:
            shutil.rmtree(work, ignore_errors=True)
    return _normalize_swift_payload(payload)


def _normalize_swift_payload(payload: dict) -> dict:
    providers = sorted(payload["providers"], key=lambda item: item["id"])
    return {"providers": providers}


# ---------------------------------------------------------------------------
# Swift surface: documented regex fallback
# ---------------------------------------------------------------------------
#
# The fallback understands exactly the shapes used in LLMProvider.swift and
# TranslationTransport.swift today:
#
#   case foo                                   enum cases (one per line)
#   case .a, .b:      return "literal"         single-value switch arms
#   case .a:          return L("zh", "en")     localized display names (en kept)
#   case .a, .b:      return [ FieldOption(value: "...", label: "..."), ... ]
#   case .a, .b:      return []                empty model lists
#   return .thinking                           ThinkingDisableField arms
#   default:          return nil
#   self == .a || self == .b                   boolean membership
#   self != .a                                 boolean exclusion
#
# TranslationProtocol.supports is reproduced from its four statements:
#   if provider == .x || provider == .y { return true }
#   if provider.modelOptions.contains(...)
#   if [.a, .b].contains(provider), model == "m" { return true }
#   provider == .p && ["m1", "m2"].contains(model) || provider == .q && model == "m"
#
# Any other shape raises InventoryError so a silent drift is impossible.

_CASE_LINE = re.compile(r"^\s*case\s+([A-Za-z_][A-Za-z0-9_]*)\s*(//.*)?$")
_SWITCH_ARM = re.compile(r"^\s*case\s+((?:\.[A-Za-z0-9_]+\s*,?\s*)+):\s*(.*)$")
_DEFAULT_ARM = re.compile(r"^\s*default:\s*(.*)$")
_RETURN_STRING = re.compile(r'return\s+"((?:[^"\\]|\\.)*)"')
_RETURN_L = re.compile(r'return\s+L\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)')
_RETURN_FIELD = re.compile(r"return\s+\.([A-Za-z]+)")
_FIELD_OPTION = re.compile(
    r'FieldOption\(value:\s*"((?:[^"\\]|\\.)*)",\s*label:\s*"((?:[^"\\]|\\.)*)"\)'
)
_VAR_BLOCK = re.compile(
    r"var\s+(?P<name>[A-Za-z]+):\s*(?P<type>[^{]+)\{\s*\n(?P<body>.*?)\n    \}\n", re.DOTALL
)
_MEMBERSHIP = re.compile(r"self\s*==\s*\.([A-Za-z0-9_]+)")
_EXCLUSION = re.compile(r"self\s*!=\s*\.([A-Za-z0-9_]+)")


def _swift_string(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _enum_cases(source: str) -> list[str]:
    match = re.search(
        r"enum LLMProvider: String, CaseIterable[^{]*\{(.*?)\n    var displayName",
        source,
        re.DOTALL,
    )
    if not match:
        raise InventoryError("LLMProvider enum header not found")
    cases = []
    for line in match.group(1).splitlines():
        found = _CASE_LINE.match(line)
        if found:
            cases.append(found.group(1))
    if not cases:
        raise InventoryError("LLMProvider enum has no cases")
    return cases


def _var_body(source: str, name: str) -> str:
    for block in _VAR_BLOCK.finditer(source):
        if block.group("name") == name:
            return block.group("body")
    raise InventoryError(f"LLMProvider.{name} not found")


def _arm_cases(spec: str) -> list[str]:
    return [item.strip().lstrip(".") for item in spec.split(",") if item.strip()]


def _switch_string_values(body: str, cases: list[str], *, localized: bool) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in body.splitlines():
        arm = _SWITCH_ARM.match(line)
        if not arm:
            continue
        rest = arm.group(2)
        localized_match = _RETURN_L.search(rest)
        if localized_match:
            value = _swift_string(localized_match.group(2))
        else:
            plain = _RETURN_STRING.search(rest)
            if not plain:
                raise InventoryError(f"unparsed switch arm: {line.strip()}")
            value = _swift_string(plain.group(1))
        for case in _arm_cases(arm.group(1)):
            values[case] = value
    missing = [case for case in cases if case not in values]
    if missing:
        raise InventoryError(f"switch arms missing for {missing}")
    return values


def _switch_model_options(body: str, cases: list[str]) -> dict[str, list[dict[str, str]]]:
    options: dict[str, list[dict[str, str]]] = {}
    current: list[str] | None = None
    for line in body.splitlines():
        arm = _SWITCH_ARM.match(line)
        if arm:
            current = _arm_cases(arm.group(1))
            if "return []" in arm.group(2):
                for case in current:
                    options[case] = []
                current = None
            else:
                for case in current:
                    options[case] = []
            continue
        if current is None:
            continue
        found = _FIELD_OPTION.search(line)
        if found:
            for case in current:
                options[case].append(
                    {"value": _swift_string(found.group(1)), "label": _swift_string(found.group(2))}
                )
        elif line.strip() == "return []":
            current = None
        elif line.strip() in {"return [", "]", ""}:
            continue
        else:
            raise InventoryError(f"unparsed modelOptions line: {line.strip()}")
    missing = [case for case in cases if case not in options]
    if missing:
        raise InventoryError(f"modelOptions arms missing for {missing}")
    return options


def _switch_thinking_field(body: str, cases: list[str]) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    pending: list[str] | None = None
    default_value: str | None = None
    saw_default = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped in {"}", "switch self {"}:
            continue
        arm = _SWITCH_ARM.match(line)
        default = _DEFAULT_ARM.match(line)
        if arm:
            pending = _arm_cases(arm.group(1))
            rest = arm.group(2)
        elif default:
            pending = ["__default__"]
            rest = default.group(1)
            saw_default = True
        else:
            rest = stripped
        if pending is None or "return" not in rest:
            if pending is None and "switch" not in stripped:
                raise InventoryError(f"unparsed thinkingDisableField line: {stripped}")
            continue
        field_match = _RETURN_FIELD.search(rest)
        value: str | None
        if "return nil" in rest:
            value = None
        elif field_match:
            try:
                value = THINKING_FIELD_VOCAB[field_match.group(1)]
            except KeyError as exc:
                raise InventoryError(f"unknown ThinkingDisableField .{exc.args[0]}") from exc
        else:
            raise InventoryError(f"unparsed thinkingDisableField return: {rest}")
        if pending == ["__default__"]:
            default_value = value
        else:
            for case in pending:
                values[case] = value
        pending = None
    if not saw_default:
        missing = [case for case in cases if case not in values]
        if missing:
            raise InventoryError(f"thinkingDisableField arms missing for {missing}")
    return {case: values.get(case, default_value) for case in cases}


def _boolean_rule(body: str, cases: list[str]) -> dict[str, bool]:
    exclusions = _EXCLUSION.findall(body)
    members = _MEMBERSHIP.findall(body)
    if exclusions and not members:
        return {case: case not in exclusions for case in cases}
    if members and not exclusions:
        return {case: case in members for case in cases}
    raise InventoryError(f"unparsed boolean rule: {body.strip()}")


_SUPPORTS_GUARD = re.compile(r"guard\s+!model\.isEmpty\s+else\s*\{\s*return false\s*\}")
_SUPPORTS_OPTIONS = re.compile(r"provider\.modelOptions\.contains\(")
_SUPPORTS_ANY = re.compile(
    r"if\s+((?:provider\s*==\s*\.[A-Za-z0-9_]+\s*(?:\|\|)?\s*)+)\{\s*return true\s*\}"
)
_SUPPORTS_LIST_MODEL = re.compile(
    r'if\s+\[((?:\.[A-Za-z0-9_]+\s*,?\s*)+)\]\.contains\(provider\),\s*model\s*==\s*"([^"]+)"\s*\{\s*return true\s*\}'
)
_SUPPORTS_MODELS = re.compile(
    r'provider\s*==\s*\.([A-Za-z0-9_]+)\s*&&\s*\[((?:"[^"]+"\s*,?\s*)+)\]\.contains\(model\)'
)
_SUPPORTS_MODEL = re.compile(r'provider\s*==\s*\.([A-Za-z0-9_]+)\s*&&\s*model\s*==\s*"([^"]+)"')


def _translation_rules(source: str) -> tuple[set[str], dict[str, set[str]]]:
    match = re.search(
        r"static func supports\(_ provider: LLMProvider, model: String\) -> Bool \{(.*?)\n    \}",
        source,
        re.DOTALL,
    )
    if not match:
        raise InventoryError("TranslationProtocol.supports not found")
    body = match.group(1)
    any_model: set[str] = set()
    extras: dict[str, set[str]] = {}
    known = (
        _SUPPORTS_GUARD,
        _SUPPORTS_OPTIONS,
        _SUPPORTS_ANY,
        _SUPPORTS_LIST_MODEL,
        _SUPPORTS_MODELS,
        _SUPPORTS_MODEL,
    )
    saw_options = False
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        # Every statement line must match a known shape, otherwise the Swift
        # table grew a rule this parser would silently ignore.
        if not any(pattern.search(line) for pattern in known):
            raise InventoryError(
                "TranslationProtocol.supports has a shape the regex fallback does not understand "
                f"({line}); run with swiftc or extend the parser"
            )
        saw_options = saw_options or bool(_SUPPORTS_OPTIONS.search(line))
        for found in _SUPPORTS_ANY.finditer(line):
            any_model.update(re.findall(r"\.([A-Za-z0-9_]+)", found.group(1)))
        for found in _SUPPORTS_LIST_MODEL.finditer(line):
            for case in re.findall(r"\.([A-Za-z0-9_]+)", found.group(1)):
                extras.setdefault(case, set()).add(found.group(2))
        for found in _SUPPORTS_MODELS.finditer(line):
            extras.setdefault(found.group(1), set()).update(
                re.findall(r'"([^"]+)"', found.group(2))
            )
        for found in _SUPPORTS_MODEL.finditer(line):
            extras.setdefault(found.group(1), set()).add(found.group(2))
    if not saw_options:
        raise InventoryError("TranslationProtocol.supports no longer consults modelOptions")
    return any_model, extras


def extract_swift_by_regex(root: Path) -> dict:
    source = (root / SWIFT_PROVIDER_FILE).read_text()
    translation = (root / SWIFT_TRANSLATION_FILE).read_text()
    cases = _enum_cases(source)
    display = _switch_string_values(_var_body(source, "displayName"), cases, localized=True)
    base_urls = _switch_string_values(_var_body(source, "defaultBaseURL"), cases, localized=False)
    options = _switch_model_options(_var_body(source, "modelOptions"), cases)
    thinking = _switch_thinking_field(_var_body(source, "thinkingDisableField"), cases)
    reasoning_split = _boolean_rule(_var_body(source, "needsReasoningSplit"), cases)
    requires_key = _boolean_rule(_var_body(source, "requiresAPIKey"), cases)
    openai_compatible = _boolean_rule(_var_body(source, "isOpenAICompatible"), cases)
    is_local = _boolean_rule(_var_body(source, "isLocal"), cases)
    any_model, extras = _translation_rules(translation)
    providers = []
    for case in cases:
        listed = [option["value"] for option in options[case]]
        accepts_any = case in any_model
        supported = set() if accepts_any else set(listed) | extras.get(case, set())
        providers.append(
            {
                "id": case,
                "display_name": display[case],
                "default_base_url": base_urls[case],
                "model_options": options[case],
                "thinking_disable_field": thinking[case],
                "needs_reasoning_split": reasoning_split[case],
                "requires_api_key": requires_key[case],
                "is_openai_compatible": openai_compatible[case],
                "is_local": is_local[case],
                "translation": {
                    "accepts_any_model": accepts_any,
                    "supported_models": sorted(supported),
                    "supported_beyond_model_options": sorted(supported - set(listed)),
                },
            }
        )
    return _normalize_swift_payload({"providers": providers})


# ---------------------------------------------------------------------------
# Keychain locations (verified against KeychainService.swift)
# ---------------------------------------------------------------------------


def keychain_locations(root: Path, provider_ids: list[str]) -> dict:
    source = (root / SWIFT_KEYCHAIN_FILE).read_text()
    required = (
        'keychainServicePrefix = "com.voxlog"',
        '"\\(keychainServicePrefix).scalar"',
        '"\\(keychainServicePrefix).grouped"',
        '"tf_llm_\\(provider.rawValue)"',
    )
    for literal in required:
        if literal not in source:
            raise InventoryError(f"KeychainService.swift no longer contains {literal!r}")
    return {
        "services": [f"{KEYCHAIN_SERVICE_PREFIX}.scalar", f"{KEYCHAIN_SERVICE_PREFIX}.grouped"],
        "account_pattern": KEYCHAIN_ACCOUNT_PATTERN,
        "accounts": {provider_id: f"tf_llm_{provider_id}" for provider_id in provider_ids},
        "source": SWIFT_KEYCHAIN_FILE,
    }


def macos_chat_path_rule(root: Path) -> dict:
    """The fixed path Vox appends to a provider base URL, verified in the Swift source."""

    source = (root / SWIFT_RESOLVER_FILE).read_text()
    literal = 'parts.path += provider == .claude ? "/messages" : "/chat/completions"'
    if literal not in source:
        raise InventoryError(f"{SWIFT_RESOLVER_FILE} no longer contains {literal!r}")
    return {
        "anthropic": "/messages",
        "openai_compatible": "/chat/completions",
        "trailing_slashes_trimmed": True,
        "source": SWIFT_RESOLVER_FILE,
    }


def runtime_chat_path_rule(root: Path) -> dict:
    """The fixed path the Python runtime appends per protocol, verified in the source."""

    source = (root / PY_RESOLVER_FILE).read_text()
    for literal in ('path = "/messages"', 'path = "/chat/completions"'):
        if literal not in source:
            raise InventoryError(f"{PY_RESOLVER_FILE} no longer contains {literal!r}")
    return {
        "anthropic": "/messages",
        "openai_compatible": "/chat/completions",
        "source": PY_RESOLVER_FILE,
    }


# ---------------------------------------------------------------------------
# Engine / Windows surface
# ---------------------------------------------------------------------------

_RUST_ENUM = re.compile(r"pub enum LlmRoute \{(.*?)\n\}", re.DOTALL)
_RUST_VARIANT = re.compile(r"^\s*(?:#\[default\]\s*)?([A-Z][A-Za-z0-9]*),\s*$", re.MULTILINE)
_TS_ENUM = re.compile(r"export const llmRouteSchema = z\.enum\(\[(.*?)\]\);", re.DOTALL)


def _kebab(variant: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", variant).lower()


def rust_llm_routes(root: Path) -> list[str]:
    source = (root / RUST_CONTRACTS_FILE).read_text()
    match = _RUST_ENUM.search(source)
    if not match:
        raise InventoryError("LlmRoute enum not found in contracts.rs")
    variants = _RUST_VARIANT.findall(match.group(1))
    if not variants:
        raise InventoryError("LlmRoute enum has no variants")
    return [_kebab(variant) for variant in variants]


def ts_llm_routes(root: Path) -> list[str]:
    source = (root / TS_SETTINGS_FILE).read_text()
    match = _TS_ENUM.search(source)
    if not match:
        raise InventoryError("llmRouteSchema not found in settings.ts")
    return re.findall(r'"([^"]+)"', match.group(1))


def python_llm_routes(route_ids: list[str]) -> list[dict]:
    """Resolve every allowlisted route with placeholder credentials, env-free."""

    from runtime.llm_provider import resolve_llm_provider
    from runtime.models.config import VoxLogConfig
    from runtime.provider_qualification import PROVIDERS

    saved = {key: os.environ.pop(key) for key in list(os.environ) if key.startswith("VOXAGENT_")}
    try:
        config = VoxLogConfig(
            openai_key="placeholder",
            dashscope_key_cn="placeholder",
            dashscope_key_us="placeholder",
            doubao_key="placeholder",
            anthropic_key="placeholder",
            custom_llm_key="placeholder",
            custom_llm_base_url="https://custom.invalid/v1",
            custom_llm_model="custom-model",
        )
        config_us = VoxLogConfig(
            dashscope_key_cn="",
            dashscope_key_us="placeholder",
        )
    finally:
        os.environ.update(saved)
    qualification = {spec.id: spec for spec in PROVIDERS if spec.kind == "llm"}
    routes = []
    for route_id in route_ids:
        resolved = resolve_llm_provider(config, route_id)
        if resolved is None:
            raise InventoryError(f"runtime resolver rejected allowlisted route {route_id!r}")
        base_urls = [resolved.base_url]
        if route_id == "qwen-turbo":
            us = resolve_llm_provider(config_us, route_id)
            if us is not None and us.base_url not in base_urls:
                base_urls.append(us.base_url)
        spec = qualification.get(route_id)
        entry: dict[str, Any] = {
            "id": route_id,
            "label": resolved.label,
            "protocol": resolved.protocol,
            "base_urls": base_urls,
            "default_model": resolved.model,
            "requires_api_key": resolved.requires_api_key,
            "local": resolved.local,
            "qualification": None,
        }
        if route_id == "openai-compatible-custom":
            entry["base_urls"] = []
            entry["default_model"] = None
            entry["user_configured_endpoint"] = True
        if spec is not None:
            entry["qualification"] = {
                "label": spec.label,
                "transport": spec.transport,
                "support": spec.support,
                "credential": spec.credential,
                "runtime": spec.runtime,
            }
        routes.append(entry)
    return routes


def build_engine_surface(root: Path) -> dict:
    rust = rust_llm_routes(root)
    ts = ts_llm_routes(root)
    if sorted(rust) != sorted(ts):
        raise InventoryError(f"LlmRoute mismatch: rust={rust} ts={ts}")
    routes = python_llm_routes(rust)
    return {
        "route_ids": rust,
        "routes": routes,
        "chat_path_rule": runtime_chat_path_rule(root),
        "sources": [RUST_CONTRACTS_FILE, TS_SETTINGS_FILE, PY_RESOLVER_FILE, PY_QUALIFICATION_FILE],
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_inventory(root: Path, swift_mode: str = "auto", sdk: str | None = None) -> dict:
    if swift_mode == "auto":
        swift_mode = "compile" if compile_available() is None else "regex"
    if swift_mode == "compile":
        swift = extract_swift_by_compile(root, sdk)
    elif swift_mode == "regex":
        swift = extract_swift_by_regex(root)
    else:
        raise InventoryError(f"unknown swift mode {swift_mode!r}")
    provider_ids = [provider["id"] for provider in swift["providers"]]
    return {
        "inventory_version": INVENTORY_VERSION,
        "product": "Vox Agent",
        "note": (
            "Generated by scripts/export_llm_provider_inventory.py. Do not edit by hand; "
            "rerun the exporter and commit the result."
        ),
        "macos": {
            "display_name_locale": "en",
            "providers": swift["providers"],
            "keychain": keychain_locations(root, provider_ids),
            "chat_path_rule": macos_chat_path_rule(root),
            "sources": [
                SWIFT_PROVIDER_FILE,
                SWIFT_TRANSLATION_FILE,
                SWIFT_KEYCHAIN_FILE,
                SWIFT_RESOLVER_FILE,
            ],
        },
        "engine_windows": build_engine_surface(root),
    }


def render(inventory: dict) -> str:
    return json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def check_snapshot(snapshot: Path, inventory: dict) -> list[str]:
    """Return unified-diff lines when the snapshot differs from the inventory."""

    expected = render(inventory)
    if not snapshot.exists():
        return [f"snapshot missing: {snapshot}"]
    actual = snapshot.read_text()
    if actual == expected:
        return []
    return list(
        difflib.unified_diff(
            actual.splitlines(),
            expected.splitlines(),
            fromfile=str(snapshot),
            tofile="regenerated",
            lineterm="",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--check", action="store_true", help="fail when the snapshot is stale")
    parser.add_argument("--stdout", action="store_true", help="print JSON instead of writing")
    parser.add_argument("--swift-mode", choices=("auto", "compile", "regex"), default="auto")
    parser.add_argument("--sdk", help="SDK path for swiftc (default: xcrun --show-sdk-path)")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    mode = args.swift_mode
    if mode == "auto":
        reason = compile_available()
        mode = "compile" if reason is None else "regex"
        if reason is not None:
            print(f"swift extraction: regex fallback ({reason})", file=sys.stderr)
    try:
        inventory = build_inventory(args.root, mode, args.sdk)
    except InventoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"swift extraction: {mode}", file=sys.stderr)
    if args.check:
        diff = check_snapshot(args.output, inventory)
        if diff:
            print("\n".join(diff), file=sys.stderr)
            print(
                f"STALE: {args.output} does not match the sources; rerun the exporter.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {args.output} matches the sources")
        return 0
    if args.stdout:
        sys.stdout.write(render(inventory))
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(inventory))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
