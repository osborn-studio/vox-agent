# ADR: Portable Vox Agent Data Contract

Date: 2026-07-19

## Context

The Windows Tauri shell had atomic settings and secure keyring migration, but
the Python compatibility runtime still defaulted to `~/.voxlog2`. The only
general migration helper wrote a separate legacy `recognition_history` schema,
and the existing history export could not be imported. Notion and Git settings
also existed only in the macOS Swift application.

Copying those implementations would keep Vox Agent coupled to VoxLog paths and
produce two incompatible sync models. Windows instead needs one application-
owned contract that the macOS and Windows Tauri builds can both read.

## Decision

### Product-owned directories

- `VOXAGENT_DATA_DIR` is the explicit override on every platform.
- Windows defaults to `%LOCALAPPDATA%\Vox Agent`.
- macOS and Linux default to `~/.voxagent`.
- A prior Windows `~/.voxagent/desktop-settings.json` is checked before
  `~/.voxlog2/desktop-settings.json`. Migration is copy-only; sources remain
  recoverable.
- The Python runtime writes new history, logs, and personal vocabulary only
  under the canonical Vox Agent directory. A legacy terms file is copied once
  when no canonical file exists.

### Versioned migration bundle

The desktop exports `*.voxagent.json` using contract version `2026-07-19.1`.
The bundle contains:

- non-ephemeral history with stable event IDs;
- dictionary corrections and protected terms;
- named modes, hotkeys, application rules, and provider route choices;
- non-secret Obsidian, Notion, and Git configuration.

The bundle never contains keyring values, provider tokens, backend tokens, Git
credentials, or a custom endpoint containing user information. Its manifest
states `secrets_included: false` and lists credentials that must be re-entered.
Unknown fields, the wrong product name/version, duplicate IDs, ephemeral
records, unsafe URLs, more than 50,000 history items, and files larger than 64
MiB are rejected before import.

### Import semantics

- History is transferred in bounded pages/batches through authenticated gateway
  routes and inserted with `INSERT OR IGNORE`.
- Stable ID conflicts are local-wins; importing the same bundle twice is
  idempotent.
- Dictionary conflicts are local-wins; new protected terms are unioned.
- Provider choices are restored, but secrets remain in the destination
  operating-system credential store and must be re-entered.
- Ephemeral history cannot be exported or imported.

The gateway advertises `data.migration.v1` and validates the same bounded data
shape before forwarding it to the Python runtime.

### Cross-device transports

- Notion sync uses the current `2026-03-11` Data Source API. The token is read
  from the operating-system keyring by Rust and never crosses the local gateway.
  Vox Agent retrieves the data-source schema, adds its missing typed properties,
  follows pagination, respects `429 Retry-After`, excludes ephemeral history,
  and imports remote rows with the same local-wins history semantics. A run
  uploads at most 200 new rows and reports the remaining count explicitly.
- Private Git sync writes one stable `devices/<device-id>/snapshot.voxagent.json`
  per installation. It invokes `git` with argument arrays rather than a shell,
  requires a dedicated clean working tree, verifies `origin`, rebases before
  publishing, and uses the system credential helper or SSH agent. No Git token
  enters settings or a snapshot. Peer history and vocabulary use the same
  bounded local-wins import path; peer settings do not overwrite local settings.

### Legacy import

Known `.voxlog2` and macOS Application Support databases are opened with SQLite
`mode=ro` and `query_only=ON`. Only the known `voice_events`, `voice_log`, and
`recognition_history` tables are accepted. Records are normalized to the
portable Vox Agent contract, generated IDs are content-stable, imports are
idempotent, and source files are never moved, marked, or modified. The desktop
does not accept an arbitrary database path from the WebView.

## Consequences

Fresh Windows installs no longer depend on a VoxLog directory, and users have a
real macOS-to-Windows path when both devices run the cross-platform Tauri app.
Portable bundles, private Git, Notion history mirroring, and copy-only legacy
history import now share one data model. Credentialed Notion/Git tests and
physical Windows sync-device tests still need release credentials. Engine-owned
state no longer defaults to VoxLog paths. SQLite uses WAL, bounded lock waits,
foreign keys, `NORMAL` synchronization, and consistent online backups; a
secret-free two-process lock probe runs on the Windows CI host. The Notion/Git
transport is not presented as credentialed hardware qualification until those
external tests are captured.
