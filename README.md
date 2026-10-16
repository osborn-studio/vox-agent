# Vox Agent

**Voice-first desktop workspace, transcription tools, and an agent execution engine.**

[中文](README.en.md#中文说明) · [Source snapshot](SOURCE_SNAPSHOT.md)

This repository publishes a reviewed **source snapshot**, not an installer or a
qualified product release. It starts with a new root commit and does not inherit
private development history, pull requests, release assets, credentials, models,
operator audit reports, or backups. Product/module identifiers such as `VoxLog`
and the development version `4.2.9` remain unchanged for compatibility.

## Source layout

- `engine/`, `runtime/`: Python execution, permissions, memory, speech, and APIs.
- `macos/`: SwiftUI desktop application and local ASR helper source.
- `apps/desktop-tauri/`: Windows-capable desktop shell.
- `packages/contracts/`, `services/api-ts/`: shared contracts and gateway.
- `apps/web-console/`, `mobile/voxagent-companion/`: web/mobile source.
- `tests/`, `scripts/`, `security/`: tests, build tools and dependency defenses.

## Local development

Python requires 3.11+. Work in a disposable virtual environment and provide your
own service configuration when needed:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,mcp]'
.venv/bin/python -m pytest tests --ignore=tests/test_engine_e2e_claude.py -q
.venv/bin/ruff check .
```

The external Claude CLI end-to-end test is opt-in and needs separate credentials.
ASR models, native dependencies, provider authentication, signing/notarization,
platform SDKs and deployment services are not supplied. Sample server addresses
and legacy update URLs in source are not a grant of access to a hosted service.
Do not run deployment scripts against infrastructure you do not own.

Other local checks are documented in `CLAUDE.md`. Commands can download build
dependencies; they do not establish a device/release qualification. No service,
model, microphone or paid provider is started by cloning this repository.

## Automation and maintenance

**GitHub Actions is disabled for this repository.** Original CI definitions are
retained as non-executable `.yml.txt` reference files in `.github/source-workflows/`.
There are no active workflows, automatic PR jobs, self-hosted runner requests,
release uploads or credential-copy steps. Tests that inspect workflow source
read those reference copies; their assertions are retained. Enabling future CI
requires a separate review of runner access, permissions, fork PRs and costs.

Private development continues separately. Public updates are reviewed source
snapshots or patches applied to this repository's own root; do not merge private
Git ancestry or copy operator evidence, private assets or secrets here. Existing
private releases are not releases of this repository.

## Licenses and omitted resources

Repository-owned code retains [MIT](LICENSE). Third-party source packages,
patches, notices and component-specific licenses remain under their own terms;
MIT does not replace those licenses. Build-required npm source archives are
retained with their notices; these are dependency source packages, not Vox Agent
installation binaries.

Recorded/test audio arrays and optional bundled sound files are omitted. The
sample generator remains available for local use; the existing macOS sound
code falls back to a synthesized chime when a bundled cue is absent. Model
weights and application binaries are not included. See [SOURCE_SNAPSHOT.md](SOURCE_SNAPSHOT.md)
for the exact source binding and export boundaries.
