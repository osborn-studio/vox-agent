# Vox Agent desktop/backend contract

The canonical Windows shell (`apps/desktop-tauri`) talks to the TypeScript API
gateway on `http://127.0.0.1:7901`. The gateway deliberately separates two
upstreams: the compatibility voice runtime on port `7902`, and the Vox Agent
engine on port `7878`. Agent, memory, capture, device, token, and admin routes go
only to the engine; voice, recent, history, provider, dictionary, and digest
routes go only to the compatibility runtime. It must complete the contract
handshake before enabling Agent commands. Recording-only backends and stale
VoxLog-era services do not qualify as compatible Vox Agent backends.

## Compatibility handshake

`GET /v1/desktop/contract` returns:

```json
{
  "api_version": "2026-07-18.1",
  "product": "Vox Agent",
  "capabilities": [
    "agent.query",
    "speech.stream.v1",
    "translation.realtime.v1",
    "voice.capture",
    "history.search",
    "data.migration.v1",
    "settings.providers",
    "digest.export"
  ]
}
```

The Rust bridge requires the exact `api_version`, the `Vox Agent` product name,
and the `agent.query` capability. A mismatch disables Agent commands and is shown
as a contract error in the desktop UI. Additive response fields remain compatible;
renaming or removing fields requires a new dated contract version and coordinated
TypeScript, Rust, and UI changes.

## Stable surface

| Capability | HTTP surface | Rust command | Shared schema |
| --- | --- | --- | --- |
| Contract | `GET /v1/desktop/contract` | `get_desktop_contract` | `desktopContractSchema` / `DesktopContract` |
| Agent query | `POST /v1/agents/query` | `query_agent` | `agentQueryRequestSchema`, `agentAnswerSchema` / `AgentQueryInput`, `AgentAnswer` |
| Realtime translation | `POST /v1/translations` | — (P3.3 UI) | `translationRequestSchema`, `translationEventSchema` |
| Voice | `POST /v1/voice` | `upload_voice` | `fastPathResponseSchema` / `FastPathResponse` |
| History | `GET /v1/history*` | `get_history`, `get_agent_history` | `historyItemSchema` / `HistoryItem` |
| Portable data | `GET/POST /v1/migration/*` | `export_migration_bundle`, `import_migration_bundle`, `import_legacy_voxlog`, `sync_git_history`, `sync_notion_history` | migration schemas / `MigrationBundle` |
| Providers | `GET/POST /v1/settings/providers*`, `GET /v1/settings/provider-qualifications` | settings and qualification commands | provider schemas / provider structs |
| Digests | `GET/POST /v1/digests*` | digest commands | digest schemas / `SessionDigest` |

The TypeScript gateway validates Agent requests before forwarding them. The Rust
bridge trims queries, rejects empty or over-8,000-character input, bounds context
results to 1–50, and deserializes the shared camel-case response contract.

Provider qualification uses its own additive contract version `2026-07-19.2`.
Its support, configuration, package, model-asset, proxy-environment, and
live-probe fields are independent: a stored cloud key is
`configured_unverified`, never qualified. A discovered local runtime is
`ready` but does not increment `qualified_count` until a real smoke has evidence.
Static refreshes do not contact third-party providers. See
`docs/decisions/2026-07-19-provider-qualification-contract.md`.

Provider settings also carry allowlisted per-profile LLM main/fallback routes
and bounded non-secret model metadata. OpenAI, Qwen, Doubao, custom compatible,
and Ollama routes share one chat-completions client; Claude uses the Anthropic
Messages wire format behind the same runtime interface. Secret inputs are
write-only and never appear in settings responses. See
`docs/decisions/2026-07-19-windows-shared-llm-provider-routing.md`.
Remote custom endpoints require HTTPS; plain HTTP is accepted only for loopback
development runtimes.

Set `VOXLOG_PY_BACKEND_URL` for the compatibility runtime and
`VOXAGENT_ENGINE_BASE_URL` for the new engine. Their credentials are separately
configurable through `VOXLOG_PY_BACKEND_API_TOKEN` and
`VOXAGENT_ENGINE_API_TOKEN`; end-user authorization headers on admin routes are
never replaced by a service credential.

## Compatibility migrations

- The canonical app identifier is `com.osborn.voxagent.desktop`.
- Global shortcut events use `vox-agent://global-hotkey`; one legacy event is
  emitted during the compatibility release for already-running WebViews.
- Non-secret settings migrate recoverably from `~/.voxlog2` to `~/.voxagent`.
- Windows stores new application data under `%LOCALAPPDATA%\Vox Agent`; a prior
  `~/.voxagent` is a copy-only compatibility source.
- Python history, logs, and personal vocabulary now default to the canonical Vox
  Agent directory. VoxLog paths are read-only migration sources.
- Keyring reads fall back to `com.voxlog2.desktop` and copy recovered credentials
  into `com.osborn.voxagent.desktop`.

Portable `*.voxagent.json` bundles use contract `2026-07-19.1`, reject unknown
or unsafe fields, exclude ephemeral records and all credentials, and merge
history/dictionary data idempotently with local values winning conflicts. See
`docs/decisions/2026-07-19-portable-vox-agent-data-contract.md`.

Private Git stores the same validated bundle at a stable per-device path and
uses only the system credential helper or SSH agent. Notion credentials stay in
the OS keyring and are consumed directly by Rust against the current Data Source
API. Previous-app SQLite databases are discovered from allowlisted locations,
opened read-only, and normalized through the portable history importer.
