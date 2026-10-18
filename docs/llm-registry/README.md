# LLM registry reconciliation (L0, no runtime change)

Owner decision of 2026-10-18: LLM provider configuration becomes a cross-app
shared layer. The Osborn-PA project publishes an **L0 pure-JSON registry**
(`osborn-llm-registry/providers@1` and `models@1`, in PA's
`packages/llm-registry/`) and will serve it read-only from PA Cloud. Vox Agent
keeps its own Swift / Python clients and will later *read* its provider data
from that registry.

**Nothing in this directory changes runtime behaviour.** The first Vox
deliverable is tooling that shows, deterministically, where Vox's current
provider truth and the registry disagree, so the later switch is a reviewed
diff instead of a guess.

## Files

| Path | Purpose |
| --- | --- |
| `docs/llm-registry/vox-provider-inventory.json` | Committed snapshot of Vox's provider truth (both surfaces). Generated; never hand-edit. |
| `scripts/export_llm_provider_inventory.py` | Produces the snapshot; `--check` fails when it is stale. |
| `scripts/llm_registry_l0.py` | The **only** module that knows the registry's field names: schema gate, URL rule, dialect defaults, structural validation, identity-binding check. |
| `scripts/reconcile_llm_registry.py` | Reads the inventory and the registry, applies the id mapping, reports every difference class (Markdown or `--json`). |
| `tests/fixtures/llm_registry_l0/` | Verbatim copies of PA's `providers.json`, `models.json`, `fixtures/urls.json`, `compat/baseline-v1.json` and the two JSON schemas, with `PROVENANCE.md` (branch + commit). |
| `tests/test_llm_registry_reconcile.py` | Tests for the PA files (structure, identity, URL rule), the mapping, every difference class, the reader's gates and the inventory `--check`. |

## What the inventory contains

Surface (a), native macOS, extracted from `macos/VoxLog/LLM/LLMProvider.swift`
and `macos/VoxLog/Translation/TranslationTransport.swift`:

- `id` (enum raw value), `display_name` (English form of `L(zh, en)`),
  `default_base_url`, `model_options` (value + label),
  `thinking_disable_field` as the Swift case name in snake case
  (`thinking` / `enable_thinking` / `reasoning_effort` / `think` / `null`),
  `needs_reasoning_split`, `requires_api_key`, `is_openai_compatible`, `is_local`;
- `translation`: the `TranslationProtocol.supports` result, as the set of
  accepted model ids (listed `modelOptions` plus the hard-coded aliases such as
  `qwen3.8-omni-flash`, `deepseek-flash`, `deepseek-v4-pro`, `gpt-4o`) or
  `accepts_any_model` for OpenRouter and Ollama;
- `keychain`: the legacy Keychain location Vox writes today, verified against
  `KeychainService.swift`: services `com.voxlog.scalar` / `com.voxlog.grouped`,
  account `tf_llm_<provider id>`;
- `chat_path_rule`: the fixed path Vox appends to a base URL (`/messages` for
  Claude, `/chat/completions` otherwise, trailing slashes trimmed), verified
  against `SelectedLLMConfigurationResolver.swift`.

Surface (b), engine / Windows, from `apps/desktop-tauri/src-tauri/src/contracts.rs`
(`LlmRoute`), `packages/contracts/src/settings.ts` (`llmRouteSchema`),
`runtime/llm_provider.py` (`resolve_llm_provider`, resolved with placeholder
credentials and no `VOXAGENT_*` environment, plus the same chat-path rule) and
`runtime/provider_qualification.py` (`PROVIDERS`, the support axis of the
three-axis qualification vocabulary). Each route carries its protocol, base
URL(s) (`qwen-turbo` lists CN then US), default model and qualification spec.

The JSON is sorted and timestamp-free, so the snapshot only changes when the
sources change.

### How the Swift surface is extracted

This Mac has no Xcode, and Linux CI has no Swift at all, so the exporter has two
paths that must agree:

1. **compile** (default where `swiftc` exists): a tiny `@main` runner is built
   with `swiftc -parse-as-library -sdk $(xcrun --show-sdk-path)` against the two
   real Swift files plus stubs for UI-only symbols (`L`, `FieldOption`,
   `TranslationConfiguration`, …) and executed. This is the authoritative path.
2. **regex** (fallback, automatic when `swiftc` is missing): a parser that
   understands exactly the statement shapes used in those files today. Any
   other shape raises an error instead of silently dropping a rule.

`tests/test_llm_registry_reconcile.py::test_compiled_swift_runner_agrees_with_regex_fallback`
asserts both paths produce identical JSON wherever `swiftc` runs.

## Running

```bash
# Regenerate the snapshot after touching LLMProvider.swift, TranslationTransport.swift,
# KeychainService.swift, SelectedLLMConfigurationResolver.swift, contracts.rs,
# settings.ts or runtime/llm_provider.py:
.venv/bin/python scripts/export_llm_provider_inventory.py

# CI gate (also run by the test suite):
.venv/bin/python scripts/export_llm_provider_inventory.py --check

# Reconcile against the committed PA copies (Markdown to stdout):
.venv/bin/python scripts/reconcile_llm_registry.py

# JSON, and non-zero exit when any counted difference is found:
.venv/bin/python scripts/reconcile_llm_registry.py --json --strict
```

### Against PA's live files once the URL arrives

The PA Cloud read-only endpoint (`GET /api/llm-registry/v1/{providers,models}.json`)
is a later PA PR. The scripts already accept `https://` URLs:

```bash
.venv/bin/python scripts/reconcile_llm_registry.py \
  --providers https://<pa-host>/api/llm-registry/v1/providers.json \
  --models    https://<pa-host>/api/llm-registry/v1/models.json \
  --urls none --baseline tests/fixtures/llm_registry_l0/baseline-v1.json \
  --output docs/llm-registry/reconciliation-$(date +%Y-%m-%d).md
```

Only `https://` is accepted. Keep `--baseline` pointed at the committed
`compat/baseline-v1.json`: it supplies the enum vocabulary and the frozen
identity bindings the live file must still honour.

To refresh the committed PA copies, re-copy the six files from PA's
`packages/llm-registry/` and update `tests/fixtures/llm_registry_l0/PROVENANCE.md`
with the branch and commit. Tests that pin the current data (model gaps,
`dashscope_intl` / `kimi_cn`, 13 URL fixtures, 15 providers, 451 models) are
expected to need their numbers adjusted then; nothing else should.

If PA bumps the schema (`providers@2`), the reader refuses the file with a
message naming `scripts/llm_registry_l0.py`; adapt the accessors there and only
there.

## Difference classes reported

| Section | Meaning | Result against PA commit `af7dfe6` |
| --- | --- | --- |
| Providers in Vox but not in the registry | Vox enum cases with no `aliases.vox` match and no agreed id present | none |
| Providers in the registry but not in Vox | registry ids no Vox provider maps to | `dashscope_intl`, `kimi_cn` (no Vox alias by design) |
| Mapped providers not `active` | registry `status` other than `active` (unknown values read as `unsupported`) | none |
| Effective chat URL mismatches | Vox `baseURL` + `/chat/completions` or `/messages` vs registry `base_url + chat_path` | none |
| Base URL split differently (informational) | same effective URL, different split | Claude: Vox `…/v1` + `/messages`, registry `https://api.anthropic.com` + `/v1/messages` |
| Registry default endpoint differs (informational) | **Gemini**: Vox uses `/v1beta/openai` (OpenAI-compatible), the registry defaults to `gemini-generate-content` | Gemini only |
| Models in Vox missing from `models.json` | Vox `modelOptions` not in the mapped provider's catalogue | 13: all 6 Doubao (PA has no Doubao catalogue yet), 4 Kimi (`kimi-k2.5`, `moonshot-v1-*`), `glm-4-long`, `glm-5-turbo`, `qwen-long` on `dashscope_us` |
| Models in `models.json` missing from Vox | the reverse; providers without `modelOptions` that accept any model id are "not compared" | 217 (models.dev is a superset; review per provider) |
| Dialect disagreements | Vox `thinkingDisableField` mapped onto the typed `thinking_disable` patch, and `reasoning_split`; absent keys take the schema defaults | none |
| Secret id and Keychain mapping | legacy vs new location, `aliases.vox_keychain_account` must equal `tf_llm_<vox id>`, `secret.required` vs `requiresAPIKey` | all match |
| Engine / Windows `LlmRoute` | each route's registry id(s) present, `active`, and effective chat URL equal | Ollama loopback host differs: macOS and registry `localhost`, runtime `127.0.0.1` (Vox-internal; settle when the runtime switches) |
| URL rule self-check | the reader's `base_url + path` rule reproduces every row of PA's `fixtures/urls.json` | 13 / 13 |
| Identity bindings | `providers.json` honours `compat/baseline-v1.json` (`secret.id`, `secret.env` order, endpoint `base_url`) | no violations |

`has_differences` (and `--strict`) ignores the two informational classes.

## Contract Vox expects from the registry (migration notes)

Agreed between the Vox and PA sessions on 2026-10-18 and now encoded in PA's
`providers.schema.json`. This is what the reconciler checks and what the later
runtime change will rely on.

### Provider id mapping

`aliases.vox` on each registry provider is authoritative. The agreed values,
which the reconciler also uses as a fallback and cross-checks:

| Vox `LLMProvider` | Registry id |
| --- | --- |
| `bailian` | `dashscope_cn` |
| `bailianUS` | `dashscope_us` |
| `kimi` | `kimi_intl` |
| `minimaxCN` | `minimax_cn` |
| `minimaxIntl` | `minimax_intl` |
| `claude` | `claude` |
| `ollama` | `ollama` |
| `doubao`, `openai`, `gemini`, `deepseek`, `zhipu`, `openrouter` | same name |

`dashscope_intl` and `kimi_cn` exist in the registry without a Vox alias. Each
provider also carries `aliases.vox_keychain_account` (= `tf_llm_<vox id>`).

### Endpoints, URL rule and dialect

`endpoints` is keyed by protocol family (`openai-chat-completions`,
`openai-responses`, `anthropic-messages`, `gemini-generate-content`) with a
`default_endpoint`. Each endpoint has `base_url`, `chat_path`, optional
`stream_chat_path`, `discovery_path`. **URL rule:** `url = base_url + path`,
`{model}` replaced by the percent-encoded model id (JS `encodeURIComponent`),
string concatenation, never URL resolution. Vox speaks `anthropic-messages` for
Claude and `openai-chat-completions` for everything else, including Gemini.

Endpoint `dialect` (absent key = schema default):

- `thinking_disable`: typed patch `{path, value}` or `null`. Vox's enum maps as
  `thinking` → `{thinking.type: "disabled"}` (Doubao, Kimi, DeepSeek),
  `enable_thinking` → `{enable_thinking: false}` (Bailian),
  `reasoning_effort` → `{reasoning_effort: "none"}` (Zhipu),
  `think` → `{think: false}` (Ollama), `nil` → `null`;
- `reasoning_split` (default false; MiniMax CN/Intl true, matching Vox);
- `stream_options` (default true), `temperature` (default true; Claude false),
  `developer_role` (default false). Not yet consumed by Vox.

Unknown `endpoint_types`, unknown dialect keys and unknown enum values are
"unsupported / ignore", never fatal. Identity bindings (`id`, `secret.id`,
`secret.env` order, an existing endpoint's `base_url`) are immutable within `@1`.

### Secrets: Keychain dual-read

Secrets are keyed by `secret.id`; `secret.env` lists environment-variable names
(PA's location), `secret.required` says whether a key is needed (Ollama: no).
Vox will read, in order:

1. new location: service `osborn.llm`, account `<secret.id>`;
2. legacy location: services `com.voxlog.scalar` / `com.voxlog.grouped`,
   account `aliases.vox_keychain_account` = `tf_llm_<vox id>` (what
   `KeychainService.swift` writes today).

Legacy data is preserved; nothing is deleted or renamed by the migration. The
reconciler's "Secret id and Keychain account mapping" table lists both
locations per provider so the dual-read table can be reviewed before it is
coded.

### Vocabulary that must stay additive

The three-axis qualification vocabulary (`support` / `readiness` /
`probe_status`, see `apps/desktop-tauri/src/settings/provider-badge.js` and the
ADR `docs/decisions/2026-07-19-provider-qualification-contract.md`) is Vox's L2
UI vocabulary. The registry may adopt it; neither side may change its values
non-additively.

## Next step

1. Review the reconciliation result above with the owner, in particular the
   13 Vox models absent from `models.json` (Doubao has no catalogue in PA yet)
   and the Ollama loopback host difference inside Vox.
2. When PA's read-only endpoint ships, run the reconciler against it with the
   command above and commit the report.
3. Only then start runtime work in its own PR: read provider data from the
   registry (keeping `OpenAICompatibleLLMConfig<Tag>` + `ClaudeChatClient`),
   apply the typed `thinking_disable` patch, and add the Keychain dual-read.
