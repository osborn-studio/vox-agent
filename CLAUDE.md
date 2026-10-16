# Vox Agent

Voice-first AI agent execution platform with a native macOS cockpit, canonical
Windows-capable Tauri shell, Python agent engine, TypeScript gateway/contracts,
web console, and mobile companion.

## Instruction authority

`CLAUDE.md` is the detailed project instruction source. Root `AGENTS.md` is the
small non-Claude bootstrap that tells Codex and other agents to open this file
and every task-relevant file it references. The two files are deliberately not
a byte-for-byte mirror: Claude Code needs direct project context here, while
non-Claude agents do not expand Claude-style imports reliably. This accepted
exception is recorded in
[docs/decisions/2026-07-18-agent-instruction-authority.md](docs/decisions/2026-07-18-agent-instruction-authority.md).

Do not replace this file with `@AGENTS.md` or a symlink. Do not modify
user-owned instruction files merely to silence a tool that only supports the
opposite mirror direction.

## Product and naming boundary

- User-facing product name: **Vox Agent**.
- `VoxLog` remains in existing Swift module/source paths and legacy persistence
  identifiers. Do not opportunistically rename those internals.
- This is an agent cockpit, not a renamed VoxLog transcript viewer: changes must
  preserve agent execution, scoped permissions, grounded citations, operations
  visibility, and voice-first control.
- Physical iOS, Android, and Windows release claims require named-device/operator
  evidence. A simulator, compile, or macOS result is not a hardware pass.

## Current verified inventory (2026-07-18)

- Python engine: 121 modules / 20,490 lines; `engine/server.py` is a 145-line
  composition root with 153 HTTP operations mounted through domain routers.
- Native macOS: 138 production Swift files plus 48 Swift test files.
- Cross-platform Rust: 10 source files across the canonical Tauri shell and two
  native crates; the current Rust suite has 7 tests.
- Shared/contracts: 21 TypeScript contract modules; the API gateway has 23
  TypeScript files and 63 tests.
- Web/mobile: 11 Web source files / 17 tests and 8 Mobile source files / 8 tests.
- Python verification: 117 `test_*.py` files; the deterministic suite currently
  passes 1,159 with four explicit environment skips.
- Repository agent skills: 16 Markdown skill definitions under `skills/`.

Counts are an orientation snapshot, not architecture. Refresh them from the
worktree when changing inventory claims; never copy an old audit baseline.

## Architecture and ownership

- `engine/`: Python scheduler, agent execution, permissions, memory, org policy,
  retention, streaming speech, tools, plugins, and integrations.
- `engine/app_context.py`: ownership root for stateful services. New services
  belong to an `AppContext`; do not add module-level singletons or rebind hacks.
- `engine/api/`: context-bound FastAPI routers. Keep `engine/server.py` as
  composition/lifecycle only.
- `packages/contracts/`: shared Zod wire contracts. Version public event and
  desktop interfaces before changing required fields or semantics.
- `services/api-ts/`: desktop/mobile gateway. Legacy VoxLog voice routes and Vox
  Agent engine routes use separate upstreams; do not merge them accidentally.
- `macos/VoxLog/`: primary native macOS cockpit using SwiftUI.
- `apps/desktop-tauri/`: sole Windows-capable desktop mainline. Do not revive or
  add a second Windows shell.
- `apps/web-console/`: administrative/operational web surface.
- `mobile/voxagent-companion/`: iOS/Android companion and device flows.
- `crates/input-output/`, `crates/stt-whispercpp/`: native platform/ASR helpers.

## Security and data boundaries

- Admin and organization operations derive identity from authenticated
  principals. Never trust `requesterId`, role, workspace, or tenant identity from
  a mutation body.
- Secrets belong in Keychain/Credential Manager/keyring-backed storage, never a
  committed `.env`, source literal, URL, log, or event payload.
- Permission requests must display exact scope and remain as actor-bearing audit
  records after approval or denial. Never add blind approval actions.
- Raw audio and transcript content must not enter security audit logs.
- Retention/deletion needs dry-run evidence, bounded targets, idempotency, and an
  audit record. Preserve recoverable legacy data during migrations.
- Tauri settings migrate from `.voxlog2` to `.voxagent` by copy; legacy data is
  preserved. Swift `VoxLog` paths remain compatibility identifiers.
- `voxlog-sync` is an external/private sync repository boundary. Do not delete,
  rename, or mutate it as part of this repository's cleanup.

## Testing and verification

```bash
# Python: full deterministic suite (external Claude E2E remains opt-in)
.venv/bin/python -m pytest -q
.venv/bin/ruff check .

# TypeScript contracts, gateway, web, and mobile
npm run check --workspace packages/contracts
npm run check --workspace services/api-ts
npm test --workspace services/api-ts
npm run typecheck --workspace apps/web-console
npm test --workspace apps/web-console
(cd mobile/voxagent-companion && npm ci && npm run typecheck && npm test)

# Canonical Rust shell
cargo fmt --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --all -- --check
cargo test --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked

# Native macOS
cd macos && swift build && swift test
```

Rust advisories are enforced by the pinned policy in
`security/rust-audit-policy.json`; follow
[docs/security/rust-advisory-audit.md](docs/security/rust-advisory-audit.md)
instead of installing or fetching an unpinned audit baseline.

Run the narrowest relevant checks while iterating, then the complete affected
surface before committing. The external Claude CLI E2E test requires explicit
authentication and is never silently counted as a deterministic pass.

## Implementation conventions

- Python: 3.11+, FastAPI, pytest, Ruff; reject booleans where numeric values are
  expected and avoid Pydantic coercion at trust boundaries.
- Swift: Swift 6.2, SwiftUI, Swift Package Manager; respect actor isolation and
  Reduce Motion/VoiceOver behavior.
- TypeScript: validate both inbound requests and upstream responses with shared
  Zod contracts; reject unknown mutation fields at the gateway.
- Rust: preserve `Cargo.lock`, use `--locked` in CI, and update vulnerable
  transitive dependencies instead of adding unexplained audit exceptions.
- Public schemas: additive optional fields are compatible; required-field,
  sequence, identity, or reconnect changes require an explicit version/capability.
- Preserve unrelated working-tree changes and never stage user-owned files by
  convenience.

## Design system

Before reading or editing any visual/UI code, read [DESIGN.md](DESIGN.md) in full.
Treat it as the binding design contract for every UI-related agent session.

For native macOS, tokens live in
`macos/VoxLog/UI/DesignSystem.swift` (`enum TF`): extend `TF`, never hardcode
colors or spacing in views. Cross-platform surfaces must preserve the same
semantic status/voice grammar and Vox Agent product identity without pretending
that Swift token symbols exist in JavaScript.

Key contracts: amber/recording are voice-exclusive; status is never color-only;
no toasts or modals for status; animation is restricted to voice surfaces;
permissions are inline and scope-visible. Do not deviate without explicit owner
approval. In review/QA, flag code that violates `DESIGN.md`.
