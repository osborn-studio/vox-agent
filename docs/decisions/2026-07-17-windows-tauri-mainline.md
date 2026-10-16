# ADR: Canonical Windows Tauri mainline

- Date: 2026-07-17
- Status: Accepted
- Owners: Desktop/Windows maintainers

## Decision

`apps/desktop-tauri` is the only active cross-platform Tauri shell. The
`app/frontend` prototype is removed from the worktree and retained in Git
history. Root npm scripts, alpha packaging, Windows CI, port documentation, and
feature-parity tracking all point to the canonical shell.

## Why this path

The selected shell already owns the shipped alpha build and has typed Rust
contracts for the V2 backend, global shortcuts, secure OS credential storage
with atomic plaintext-secret migration, history, recent-output recovery,
digests, and export. The retired prototype had a broader React mock-up but also
embedded a fixed bearer token, developer-oriented paths, plaintext `.env`
loading, and a second process supervisor. Keeping both made security fixes and
Windows parity ambiguous.

## Compatibility and migration

- Backend contracts remain unchanged; the canonical shell continues to use the
  configured local bridge URL and bearer token from the OS credential store.
- Unique Windows icon assets were copied into the canonical shell before the
  prototype was removed.
- No user data is moved or deleted. Legacy `.env` secrets are copied into the
  OS credential store before allowlisted assignments are atomically scrubbed.
- The retired source is recoverable from commits before this ADR, but receives
  no builds, fixes, or compatibility guarantees.

## Security boundary

No token or provider key may be compiled into frontend or Rust source. Secrets
belong in the OS credential store; non-secret settings use the per-user data
directory. Backend URLs must be user-configurable, loopback by default, and
must not contain developer-specific absolute paths.

## Exit criteria for Windows GA

- Windows CI compiles and tests the canonical Rust shell on every change.
- A signed MSI or NSIS installer starts on a clean Windows machine.
- WASAPI capture, global hotkey, SendInput/clipboard fallback, credential
  storage, engine supervision, tray/autostart, and uninstall are evidenced on
  physical Windows hardware.
- Feature-parity rows may move to Done only when code and hardware evidence are
  both linked; unvalidated code remains explicitly pending.

## Consequences

The team maintains one desktop contract surface and one threat model. The
tradeoff is that React-only visual experiments from the prototype are not
carried forward automatically; desired UX must be reintroduced against the
canonical typed bridge rather than reviving a second shell.
