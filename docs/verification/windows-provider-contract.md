# T28a: speech provider contract and feasibility for Windows

Scope: speech recognition (ASR) providers of the Vox Agent macOS baseline and
their Windows answer, for the 4.2.9 plan (`windows-v4.2.8` plan files).

This document is a contract, not a qualification.
- A provider counts as qualified only after an explicit probe with real audio
  passes (`probe_status: passed`).
- Neither a stored credential nor an installed runtime is enough.
- Until a probe passes, the Windows settings show it as "configured · not
  verified" or "installed · not verified", never "ready" or "qualified".

Decisions this follows:
- [`docs/decisions/2026-07-19-provider-qualification-contract.md`](../decisions/2026-07-19-provider-qualification-contract.md):
  qualification is evidence, not credential presence; `qualified_count` counts
  passed probes only.
- [`docs/decisions/2026-07-19-windows-asr-qualification.md`](../decisions/2026-07-19-windows-asr-qualification.md):
  - Sherpa-ONNX/SenseVoice is the recommended installable Windows local path.
  - Qwen3-ASR local is experimental on Windows.
  - Adapters fail closed: no fabricated transcripts.

The machine-readable form is `runtime/provider_baseline.py`. Its test
(`tests/test_provider_baseline.py`) reads the macOS registry
(`macos/VoxLog/ASR/ASRProviderRegistry.swift`) from source, so a provider added
on macOS without a Windows answer fails the build.

## Baseline mapping

| macOS id | macOS status | Windows answer | Follow-up |
|---|---|---|---|
| `sherpa` | implemented (local Qwen3-ASR-0.6B via WebSocket server) | `qwen-local`, experimental. Recommended Windows local path: `sherpa-sensevoice` | T28b device sample |
| `openai` | implemented | `openai-whisper` | live probe |
| `bailian` | implemented (streaming) | `qwen-cn` (batch on Windows) | live probe |
| `bailianUS` | implemented | `qwen-us` | live probe |
| `apple` | implemented | GAP `platform_unavailable` (Apple Speech is part of macOS) | none |
| `volcano` | implemented | GAP `deferred_cloud`, shown as "Additional cloud ASR family" (deferred) | T33 |
| `deepgram` | implemented | GAP `deferred_cloud` | T33 |
| `assemblyai` | implemented | GAP `deferred_cloud` | T33 |
| `elevenlabs` | implemented | GAP `deferred_cloud` | T33 |
| `soniox` | implemented | GAP `deferred_cloud` | T33 |
| `baidu` | implemented | GAP `deferred_cloud` | T33 |
| `azure` | configuration only | GAP `not_implemented_on_macos` | none |
| `google` | configuration only | GAP `not_implemented_on_macos` | none |
| `aws` | configuration only | GAP `not_implemented_on_macos` | none |
| `aliyun` | configuration only | GAP `not_implemented_on_macos` | none |
| `tencent` | configuration only | GAP `not_implemented_on_macos` | none |
| `iflytek` | configuration only | GAP `not_implemented_on_macos` | none |
| `custom` | configuration only | GAP `not_implemented_on_macos` | none |

Providers that exist only in the Windows matrix:
- `siliconflow-whisper` (SiliconFlow SenseVoice, cloud);
- `whispercpp-local` (optional);
- `sherpa-sensevoice` (recommended local).

They have no macOS baseline row, so they are not parity claims.

## Feasibility and evidence still required

| Path | Feasibility | Evidence that qualifies it | Status |
|---|---|---|---|
| `sherpa-sensevoice` CPU | Prebuilt Windows x64 wheels exist (ADR) | T28b on a named Windows x64 device: `scripts/verify_windows_asr.py --provider sherpa-sensevoice --audio SAMPLE.wav`, with RTF, first-result latency and memory recorded. RTF > 1 is a GAP | NOT_RUN |
| `qwen-local` | Heavier model flow, unproven on Windows | Same command with `--provider qwen-local`; until it passes, experimental | NOT_RUN |
| `whispercpp-local` | Optional | Same command with `--provider whispercpp-local` | NOT_RUN |
| Cloud (`openai-whisper`, `qwen-cn`, `qwen-us`, `siliconflow-whisper`) | HTTP batch adapters | A credentialed live probe per provider (costs usage; needs owner authorization) | NOT_RUN |
| Deferred cloud family | Not ported | T33 or a later provider task | GAP |

The evidence command stores only:
- the provider;
- the latency;
- the audio byte count;
- the transcript character count;
- the transcript SHA-256.

It never stores transcript content.

## What the Windows UI shows

The settings card colours a provider green ("qualified") only when
`probe_status` is `passed` (`apps/desktop-tauri/src/settings/provider-badge.js`):

| State | Label |
|---|---|
| A local runtime whose package and assets are present | "installed · not verified" |
| A stored cloud credential | "configured · not verified" |
| A deferred provider | "not implemented" |
| A missing runtime or credential | its missing item |
