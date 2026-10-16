# Vox Agent Desktop Tauri

Canonical Windows and cross-platform desktop shell for Vox Agent.

This app owns the alpha desktop build and the Windows port. It uses a Rust/Tauri
shell plus a versioned, typed bridge to the Vox Agent service layer. The gateway
keeps the Vox Agent engine separate from the compatibility voice runtime so
grounded Agent commands cannot silently fall back to a recorder-only backend. The earlier
`app/frontend` prototype was retired after the mainline decision recorded in
`docs/decisions/2026-07-17-windows-tauri-mainline.md`.

## Implemented scope

- Global hotkey and permission shell
- Desktop/backend contract version handshake with fail-closed Agent commands
- Grounded Agent command entry point backed by `/v1/agents/query`
- Dedicated recognition vocabulary workspace backed by the shared dictionary API
- Persistent named capture/output modes that apply real target, risk, and privacy controls
- History replay, native clipboard fallback, and Windows-safe Markdown/JSON exports
- Browser/WebView audio capture and upload lifecycle
- Native cpal/WASAPI capture normalized to 16 kHz mono WAV, with WebView fallback
- Hold/toggle global hotkey behavior and capture-scoped Escape cancellation
- Windows SendInput output with clipboard fallback
- Managed local Agent engine lifecycle, health, persistent database, and logs
- Tray actions, notifications, autostart, and safe existing-path opener
- Recent utterance contract bridge
- Output/undo command bridge
- Event contract types shared with the Python runtime
- Portable bundle export/import and copy-only previous-app history import
- Real Notion Data Source history sync with keyring-only tokens
- Private Git per-device snapshot sync through the system credential helper or SSH agent

## Remaining Windows hardware work

- WASAPI hardware evidence and long-session capture tests
- SendInput/clipboard validation across Notepad, browsers, VS Code, and elevated targets
- Installed tray, notification, autostart, and bundled-engine validation
- Bundled Python/local-ASR sidecar and signed updater channel

The checked-in MSI/NSIS configuration and Windows CI compile gate validate the
portable code path; device-only evidence remains tracked in the parity checklist.

## Verification

```bash
cargo test --manifest-path src-tauri/Cargo.toml --locked
npm run build  # creates the platform bundle; MSI/NSIS requires Windows
```

## Layout

```text
apps/desktop-tauri/
├── package.json          # desktop scripts and Tauri CLI dependency
├── src/                  # production cockpit HTML, CSS, and JavaScript
└── src-tauri/
    ├── Cargo.toml
    ├── tauri.conf.json
    └── src/
        ├── commands.rs
        ├── contracts.rs
        ├── bridge.rs
        ├── history_export.rs
        ├── secrets.rs
        ├── state.rs
        ├── lib.rs
        └── main.rs
```

## Credential storage and migration

Provider keys and the backend bearer token are stored in the operating-system
credential store through the Rust `keyring` crate. On first launch, the shell
checks `~/.voxlog2/.env` and `~/.voxlog/.env` for the four legacy provider API keys.
It copies missing credentials into the system store and atomically removes only
those secret assignments from the files, preserving non-secret settings and
comments. If any secure-store write fails, the source file is left unchanged.

Opening Settings rehydrates the running local backend from the credential store;
the backend does not need to persist those values itself.

Non-secret desktop settings live at `~/.voxagent/desktop-settings.json` on
macOS/Linux and `%LOCALAPPDATA%\Vox Agent\desktop-settings.json` on Windows.
`VOXAGENT_DATA_DIR` overrides either location.
Updates use an atomic replace and the file is owner-only on Unix. Named modes are
stored in this same non-secret settings contract. History exports are generated
under the product data directory's `exports` folder with application-owned,
Windows-safe filenames. Portable `*.voxagent.json` bundles live under
`migrations` and carry history, vocabulary, modes, provider choices, and
non-secret sync settings. Credentials are never included.
The same contract backs private Git sync under
`devices/<device-id>/snapshot.voxagent.json`; peer history and vocabulary merge
idempotently while local settings remain local. Notion sync reads its token
directly from the operating-system keyring and mirrors only non-ephemeral
history. Preserved previous-app databases are opened read-only and imported
without changing the originals.
Python engine state also resolves through this same product directory. SQLite
connections use WAL, a five-second busy timeout, foreign keys, and `NORMAL`
synchronous mode. Run `py scripts/verify_windows_sqlite.py --require-windows`
on Windows to capture a secret-free two-process file-lock and integrity record.
On first launch, an existing `~/.voxlog2/desktop-settings.json` is copied to the
new location while the original is preserved for rollback. Credentials are
likewise migrated lazily from the legacy keyring service to the Vox Agent service.

The legacy macOS alpha installer also imports or generates the backend service token
directly in Keychain. Its generated launch scripts retrieve that token at
process start, so the token no longer needs a plaintext `.env` assignment.
