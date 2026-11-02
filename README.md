# Vox Agent

**A voice-first AI agent cockpit: speak a task, watch agents execute it under
scoped permissions, and review grounded results.**

[English](README.md) · [简体中文](README.zh-CN.md)

Vox Agent combines a native macOS cockpit, a Windows-capable desktop shell, a
Python agent engine, a TypeScript gateway with shared wire contracts, a web
console and a mobile companion. Voice is the primary input: dictation streams
into the composer, is polished by your configured LLM, and is dispatched to
agents whose permission requests stay inline, scope-visible and audited.

> This repository publishes **source code** plus unsigned CI test builds
> (see [Install a desktop build](#install-a-desktop-build)), not a qualified
> release. See [Source provenance](#source-provenance) and
> [Limitations](#limitations) before building or deploying anything.

## Components

| Path | What it is |
|---|---|
| `engine/`, `runtime/` | Python 3.11+ engine: scheduler, agent execution, permissions, memory, org policy, retention, streaming speech, tools, plugins and FastAPI routers (`engine/api/`). |
| `macos/` | Primary native macOS cockpit (SwiftUI, Swift 6.2, Swift Package Manager). Module and persistence identifiers keep the legacy `VoxLog` name for compatibility. |
| `apps/desktop-tauri/` | The single Windows-capable desktop shell (Tauri + Rust). |
| `packages/contracts/` | Shared Zod wire contracts for events and desktop/mobile interfaces. |
| `services/api-ts/` | TypeScript gateway for the desktop and mobile clients. |
| `apps/web-console/` | Administrative and operational web console. |
| `mobile/voxagent-companion/` | iOS/Android companion (Expo) with a hash-pinned dependency supply chain. |
| `crates/` | Native helpers for input/output and whisper.cpp speech recognition. |
| `tests/`, `scripts/`, `security/` | Test suites, build/verification tools and dependency defenses. |
| `docs/` | API reference, design decisions, security notes and contracts. |

Design rules for every UI surface are in [DESIGN.md](DESIGN.md). Contributor
and agent instructions are in [CLAUDE.md](CLAUDE.md) (bootstrapped for other
agents by [AGENTS.md](AGENTS.md)).

## Install a desktop build

Unsigned test builds of both desktop apps are produced on GitHub-hosted runners
by two manual workflows. They are CI builds, not qualified releases: a CI build
is not a device pass.

Published builds are attached to the repository's
[**Releases**](https://github.com/osborn-studio/vox-agent/releases) page with a
combined `SHA256SUMS.txt`; that is the easiest place to download, and needs no
sign-in. A maintainer creates them as drafts with the **Publish Release
(draft)** workflow and publishes them after review. Between releases, the
per-run builds are available as workflow artifacts:

1. Open the repository's **Actions** tab and pick **Release DMG** (macOS) or
   **Release Windows**. Choose a successful run on `main`; a maintainer can
   start a new one with **Run workflow**.
2. On the run's summary page, download the artifact under **Artifacts**. You
   must be signed in to GitHub, and artifacts expire after 30 days.
   - `vox-agent-v<version>-dmg`: `VoxAgent-v<version>-cloud.dmg` and
     `SHA256SUMS.txt`
   - `vox-agent-v<version>-windows`: `VoxAgent-v<version>-windows-x64-setup.exe`
     (NSIS, per-user), `VoxAgent-v<version>-windows-x64.msi` and
     `SHA256SUMS.txt`
3. Check the download against `SHA256SUMS.txt`:
   `shasum -a 256 -c SHA256SUMS.txt` on macOS, or
   `Get-FileHash .\VoxAgent-v<version>-windows-x64-setup.exe -Algorithm SHA256`
   in PowerShell, compared with the matching line.

**macOS.** Open the DMG and drag **Vox Agent** to Applications. The app is
ad-hoc signed and not notarized, so on first launch right-click it and choose
**Open**, then confirm.

**Windows 10/11 (x64).** Run the `setup.exe` (or the MSI). The installers are
not Authenticode-signed, so SmartScreen shows *Windows protected your PC*:
choose **More info**, then **Run anyway**. If the Microsoft Edge WebView2
runtime is missing, the installer downloads and installs it. The app starts its bundled engine and
voice backend itself; add a speech (ASR) key and an LLM key under **Settings**
before dictating. Local ASR models are not bundled yet.

## Getting started

Clone the repository and work in a disposable virtual environment. No service,
model, microphone or paid provider starts just by cloning.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,mcp]'
npm ci
```

### Checks

Run the narrowest relevant checks while iterating, then the whole affected
surface before committing.

```sh
# Python (the external Claude CLI end-to-end test is opt-in)
.venv/bin/python -m pytest -q
.venv/bin/ruff check .

# TypeScript contracts, gateway, web and mobile
npm run check --workspace packages/contracts
npm run check --workspace services/api-ts
npm test --workspace services/api-ts
npm run typecheck --workspace apps/web-console
npm test --workspace apps/web-console
(cd mobile/voxagent-companion && npm ci && npm run typecheck && npm test)

# Windows-capable desktop shell (Rust)
cargo fmt --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --all -- --check
cargo test --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked

# Native macOS (needs Xcode 26 / Swift 6.2)
cd macos && swift build && swift test
```

## Continuous integration

GitHub Actions runs on GitHub-hosted runners only; no self-hosted runner is
reachable from pull requests.

- [`verify.yml`](.github/workflows/verify.yml) runs on every pull request and
  on pushes to `main`: `swift-build`, `python-test` (3.11 and 3.12),
  `python-lint`, `typescript-test`, `mobile-test`, `windows-tauri-test` and the
  pinned RustSec advisory audit.
- [`release-dmg.yml`](.github/workflows/release-dmg.yml) is a manual,
  ad-hoc-signed macOS DMG build. The DMG is a workflow artifact only; nothing
  is published as a release.
- [`qwen-local-spike.yml`](.github/workflows/qwen-local-spike.yml) is a manual
  Windows CPU feasibility probe for local Qwen ASR that reports hashes, sizes
  and timings only.

The RustSec job checks against a pinned advisory database with a 14-day age
limit. Refresh it as described in
[docs/security/rust-advisory-audit.md](docs/security/rust-advisory-audit.md).
Mobile dependency pins are described in
[docs/security/mobile-dependency-defenses.md](docs/security/mobile-dependency-defenses.md).
The original private workflow definitions are kept, inactive, in
[`.github/source-workflows/`](.github/source-workflows/README.md) because some
tests inspect them.

## Security boundaries

- Admin and organization operations take identity from the authenticated
  principal, never from a request body.
- Secrets belong in Keychain, Credential Manager or keyring-backed storage, never
  in committed files, URLs, logs or event payloads.
- Permission requests show their exact scope and remain actor-bearing audit
  records after approval or denial. There is no blind approval action.
- Raw audio and transcript content never enter security audit logs.
- Retention and deletion need dry-run evidence, bounded targets, idempotency and
  an audit record.

The HTTP and WebSocket surface is documented in [docs/API.md](docs/API.md);
architecture decisions are in [docs/decisions/](docs/decisions/).

## Source provenance

This public repository started from a reviewed snapshot of the maintained
source tree on 2026-10-16, with a new root commit and no private history, pull
requests, release assets, credentials, models, audit reports or backups. Details
are in [SOURCE_SNAPSHOT.md](SOURCE_SNAPSHOT.md). Development has continued here
through reviewed pull requests since then. The development version is recorded
in [docs/RELEASE.md](docs/RELEASE.md).

## Limitations

- ASR models, native dependencies, provider credentials, signing/notarization,
  platform SDKs and hosted services are not supplied.
- Sample server addresses and legacy update URLs in the source do not grant
  access to any hosted service. Do not run deployment scripts against
  infrastructure you do not own.
- Passing checks, a simulator run or a macOS build is not a device or release
  qualification. Physical iOS, Android and Windows claims need named-device
  evidence.
- Recorded test audio and optional bundled sound cues are omitted; the macOS
  app falls back to a synthesized chime.

## License

Repository-owned code is under the [MIT License](LICENSE). Third-party source
packages, patches and notices keep their own licenses; MIT does not replace
them.
