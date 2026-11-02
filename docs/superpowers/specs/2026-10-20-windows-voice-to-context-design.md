# Windows voice-to-Context flow: design

- Date: 2026-10-20 (revised after Codex `gpt-6-astra` audit, same day)
- Status: Draft for owner review
- Scope: `apps/desktop-tauri` (Windows build), `apps/desktop/server.py` + `runtime/`
  (voice backend), `engine/model_manifests`, Windows CI and device evidence
- Owner decisions recorded here: local ASR is Qwen3-ASR-0.6B; polish uses the
  shared LLM provider resolver with Qwen (Bailian), as macOS does; secrets stay
  in Windows Credential Manager; delivery reuses the Context record path to the
  Feishu group "Open Context Flow"; every PR is audited by Codex CLI
  (`gpt-6-astra`, medium) and the shipped build is verified on the owner's
  Windows PC.

## 1. Goal

A Windows user presses the global hotkey (or the mic button), speaks, and gets
the same result as on macOS 4.2.10: the transcript appears in the group
composer, is polished by the configured Qwen model, and on Send is delivered
as a Context record to the Feishu group "Open Context Flow" through
`/v1/input-backups`. Only this basic flow is in scope. Translate, Feishu
conversation sending, Mission Control and agent execution are out of scope.

## 2. Where the flow breaks today (verified 2026-10-20)

| Stage | macOS | Windows Tauri shell | Verdict |
|---|---|---|---|
| Capture | `RecognitionSession` | `audio.rs` cpal/WASAPI → 16 kHz WAV, WebView fallback | exists, no device evidence |
| ASR | cloud or local Qwen3-ASR (MLX) | `upload_voice` → gateway 7901 → Python backend 7902 → `runtime/fastpath/stt.py`. **Nothing launches 7901/7902 on Windows.** `QwenLocalASR` exists (`stt.py:341`) but loads `Qwen/Qwen3-ASR-0.6B` from the Hugging Face hub by name, picks MPS or CPU, has no pinned local files and is not packaged | blocked |
| Polish | `DoubaoChatClient` before send | only slow-path `Polisher` after the fact; the fast-path response echoes `polished_text = display_text` (`server.py:216`), so a client cannot tell "polished" from "not polished" | missing |
| Composer | `ChatInputBar` | transcript written to a status label or injected into the focused app | missing |
| Send | `saveTextMessage` → `InputBackupClient` | `context_submit_record` → `ContextService` built with **no journal** (`context_account.rs:412-417`) → `journal-unavailable` | blocked |
| Feishu session | `FeishuAccountClient` restore/renew | endpoint only from `VOX_AGENT_CONTEXT_ENDPOINT`; no `/me`, no `renew` | partial |
| Backend auth | n/a | `verify_token` passes every request when no token is configured; CORS allows `*` (`server.py:131-147`) | must harden before bundling |

Evidence baseline: `docs/releases/4.2.9-GAP.md` (F13–F19 GAP, T49p NOT_RUN),
`docs/verification/windows-provider-contract.md` (all local ASR NOT_RUN on a
named device), `docs/decisions/2026-07-19-windows-asr-qualification.md`
(fail-closed adapters; Qwen local "experimental until proven on Windows").

## 3. Architecture

```
hotkey / mic ──► audio.rs (WASAPI, 16 kHz WAV)
                      │ upload_voice(polish=1)   Bearer <per-launch token>
                      ▼
          voxagent-backend.exe  :7902   (frozen apps/desktop/server.py, launched
          ├─ /v1/voice            by engine_supervisor next to voxagent-engine.exe)
          │    ├─ STT: QwenLocalASR (qwen-asr, torch; pinned files under ~/.voxagent/models)
          │    │       local-only fallback: sherpa-sensevoice (no cloud unless opted in)
          │    └─ Polish (sync, opt-in): PolishOutcome via resolve_llm_provider(profile.llm_main)
          └─ /v1/text/polish      (typed drafts: "Polish, then send")
                      │ {display_text, polish_status, polished_text | polish_error}
                      ▼
          group composer (#agent-query via composer.setDraft)
                      │ Send → context_submit_record
                      ▼
          ContextService + encrypted journal (DPAPI key provider, owner-only ACL)
                      │ context_delivery → POST https://api.osbornvox.com/v1/input-backups
                      ▼
          Feishu group "Open Context Flow" (target id from redeem target_ids)
```

### 3.1 One shell, two bundled processes, authenticated loopback

The engine (`voxagent-engine.exe`, :18900) stays the agent control plane (ADR
2026-07-19 desktop-engine-operations). The voice backend is the existing
compatibility runtime `apps/desktop/server.py`, frozen with the already-present
`scripts/frozen_backend.py` into `voxagent-backend.exe` (:7902) and launched by
`engine_supervisor.rs` with the same lifecycle rules (health probe, auto-start,
visible unavailable state, never a silent fallback). The TypeScript gateway
(:7901) is not bundled on Windows; `backend_base_url` defaults to :7902 when the
bundled backend is present. Every route the basic flow needs (`/v1/voice`,
`/v1/voice/text`, `/v1/settings/providers*`, `/v1/dictionary`, `/v1/history`,
`/v1/recent`) is served by the Python backend; the gateway only proxied them.
Gateway-only features (`/v1/agents/query`, streaming speech, translation) are
out of scope and keep their current "unavailable" states.

Authentication is mandatory for the bundled backend:

- The supervisor generates a per-launch random token and passes it to the
  backend through an inherited environment variable (never argv, never a file);
  the backend started with `--require-auth` refuses to serve without it and
  `verify_token` no longer has a "no token configured" bypass in that mode.
- CORS is disabled in bundled mode (the WebView never calls the backend
  directly; Rust does). Dev mode keeps today's behaviour.
- The health probe is authenticated: the shell accepts :7902 as its backend
  only when `GET /health` succeeds with the launch token and echoes the
  supervisor's launch id, so a foreign process on the port is never trusted
  with provider keys.
- Provider settings and keys are pushed to the backend right after the
  authenticated health probe and again after any backend restart, not only when
  the Settings window opens. First capture therefore has credentials.

Rejected: mounting the compatibility routes inside the engine process. The
backend keeps module-level state (`_config`, `_store`, `_recent`) that CLAUDE.md
forbids in the engine, and the two have different auth models. A second
supervised binary is the smaller change.

### 3.2 Local ASR: Qwen3-ASR-0.6B on Windows

- Extend the existing `QwenLocalASR` (route ids `qwen-local` / `qwen-0.6b`)
  rather than adding a new adapter: load from a pinned local directory instead
  of the hub name; device order CUDA → MPS → CPU; `float32` on CPU; one
  process-wide inference lock (CPU inference is not reentrant-safe at useful
  speed); cancellation-safe temp files (the WAV is deleted after the executor
  future settles, not when the awaiting coroutine is cancelled). Fail-closed
  rules from ADR 2026-07-19 stay: missing runtime, missing/invalid files,
  inference error or empty result raise `STTError`.
- Model files are pinned by a new manifest
  `engine/model_manifests/qwen3-asr-0.6b.json` carrying the same nine files,
  sizes and SHA-256 values that `macos/VoxLog/Services/QwenModelCatalog.swift`
  pins (revision `5eb14417…`), so both platforms verify identical weights.
  Download uses the existing pinned-download path into
  `~/.voxagent/models/qwen3-asr-0.6b`; `~/.voxlog/models` stays a read-only
  fallback.
- **Local routes never fall back to the cloud unless opted in.** `transcribe()`
  gains a rule: when the main route is local and the fallback route is a cloud
  provider, the fallback is used only if the profile sets
  `allow_cloud_fallback: true` (default false on Windows). Otherwise the
  fallback is restricted to local routes and the error is surfaced.
- Packaging: `voxagent-backend.exe` bundles CPU torch and transformers (the
  `qwen-asr` package pins `transformers==4.57.6`). Expected size 400–600 MB plus
  a 1.9 GB first-use model download. GPU torch is not bundled; "a user-installed
  CUDA torch is picked up" is not claimed until a frozen build proves it.
- `sherpa-sensevoice` remains the documented fallback and the matrix's
  "recommended" local route until Qwen local has named-device evidence. The
  `qwen-local` entry moves to "qualified" only after the T28b probe passes on
  the owner's PC (PR-F), never in the packaging PR.
- Latency threshold (the only tunable): if a 10-second utterance takes longer
  than 6 s warm on the owner's PC CPU, new Windows installs default to
  `sherpa-sensevoice` and Qwen local is opt-in.

### 3.3 Synchronous polish

- `/v1/voice` gains a form field `polish` (`"0"` default, `"1"` to polish
  inline). The response adds `polish_status` ∈ {`"ok"`, `"skipped"`,
  `"failed"`}, keeps `polished_text` (equal to `display_text` unless
  `polish_status == "ok"`), and adds `polish_error` on failure containing only
  the provider label and the failure class (HTTP status, timeout, not
  configured), per ADR 2026-07-19 windows-shared-llm-provider-routing. Clients
  must key on `polish_status`, never on text equality.
- New `Polisher.polish_outcome(text, config) -> PolishOutcome` returns the
  structured result; the existing `polish()` keeps its `None` contract for the
  slow path. The whole operation is bounded by `asyncio.wait_for(…, 15)` as a
  total deadline on top of the HTTPX per-request timeout.
- New `POST /v1/text/polish` `{text}` → `{polish_status, polished_text,
  polish_error?}` for typed drafts ("Polish, then send").
- Route and keys: the Qwen Bailian route resolves through
  `resolve_llm_provider(config, profile.llm_main)` and uses the DashScope CN/US
  keys (`dashscope_key_cn` / `dashscope_key_us`), which the shell already
  stores in Credential Manager and pushes with the provider settings. The
  `custom_llm_key` belongs to the custom route and is not involved.
- The prompt is the shared `POLISH_PROMPT`; no per-mode prompts on Windows in
  this scope. The slow path still runs for history and digests.

### 3.4 Composer integration (WebView)

- When the cockpit window is focused and the Context group target is selected,
  a hotkey or mic capture ends by placing the backend result into
  `#agent-query` via `composer.setDraft`: `polished_text` when
  `polish_status == "ok"`, otherwise `display_text` with an inline failure note.
  When another app is focused, behaviour is unchanged (SendInput injection).
- States mirror macOS: "润色中…" with Send disabled, failure note inline with a
  "直接发送原文" action, a polished voice draft is not polished again, Escape
  discards. Typed drafts call `/v1/text/polish` on Send unless already polished.
- Send goes through the existing `submitContext` → `context_submit_record`
  path; the destination is the account's Open Context Flow target.
- Implemented in `src/context/ui.js`, `src/context/composer.js`,
  `src/main.js` (capture result routing) and `src/context/conversation-draft.js`
  (already written, currently used only by tests).

### 3.5 Encrypted journal on Windows

- New `DpapiKeyProvider` in `src-tauri/src/context_keys/` using
  `CryptProtectData` / `CryptUnprotectData` with user scope and an app-specific
  entropy string, selected on `cfg(windows)`; `key_store` reports `"dpapi"`.
- `ContextService::new` receives a real `Journal` on Windows
  (`context_account.rs:412-417`). The journal lives under
  `%LOCALAPPDATA%\Vox Agent\context\`. The directory, database, sidecar files
  and the wrapped-key file are created with an owner-only DACL
  (`windows-sys` `Win32_Security_Authorization`), and the ACL is verified at
  startup. **If the ACL is not owner-only, the journal is reported unavailable
  and delivery stays blocked**; a warning is not enough.
- Existing journal tests (`tests/context_journal.rs`, `context_record_e2e.rs`)
  run unchanged with the DPAPI provider behind the same trait; Windows-only
  tests round-trip a key through DPAPI and reject an over-permissive directory.

### 3.6 Feishu session on Windows

- Installed builds default the Context endpoint to `https://api.osbornvox.com`
  (overridable by `VOX_AGENT_CONTEXT_ENDPOINT` for development), so sign-in
  works without environment variables (T49p).
- Add `/me` on startup and `renew` before access expiry, mirroring
  `FeishuAccountClient` (1-hour access token, refresh token in Credential
  Manager). Failure reasons map to the same fixed status lines as macOS
  4.2.10's renewal diagnostics.

## 4. Security and data boundaries

- No key or token in source, argv, logs or event payloads; provider keys and
  the refresh token stay in Credential Manager; the backend launch token lives
  only in process memory and the inherited environment; the journal key is
  DPAPI-wrapped.
- Polish failures never send silently; the user chooses "send as typed".
- Local ASR routes never upload audio to a cloud provider without the explicit
  profile opt-in.
- Transcript text never enters audit logs; evidence scripts record hashes and
  lengths only.
- Delivery remains idempotent per `eventId` through the existing Context record
  contract; `polish_status` and `polished_text` are backend response fields, not
  Context record fields.

## 5. Verification

1. **Feasibility spike first (PR-0).** Before any packaging work: freeze
   `qwen-asr` + CPU torch with PyInstaller on `windows-latest`, run the frozen
   executable offline against the nine pinned files on a clean machine (no
   Python, no CUDA), and record imports/native DLL load, processor assets, dtype,
   transcript correctness on a fixed WAV, RAM, cold and warm latency, clean
   shutdown. Output is a short report in `docs/verification/`; it decides
   whether Qwen local ships as default, as opt-in, or waits.
2. **CI (GitHub-hosted only).** `windows-tauri-test` additionally runs the Node
   test suite and `scripts/windows_local_regression.py --ci`, builds
   `voxagent-backend.exe` with `scripts/build_windows_backend.py` (new, same
   shape as `build_windows_engine.py`) and runs its authenticated loopback
   health check. New pytest coverage: `QwenLocalASR` pinned loading with a stub
   model, local-only fallback rule, `/v1/voice` with `polish=1` and
   `/v1/text/polish` with a fake provider (ok / failed / timeout), manifest hash
   checks, `--require-auth` refusing unauthenticated requests.
3. **Codex audit per PR.** `codex review` with `gpt-6-astra` at medium effort on
   each PR diff, plus one `codex exec` adversarial pass per PR against a fixed
   checklist (secret leakage, fail-open ASR/polish/auth paths, journal
   availability and ACLs, endpoint defaults, Windows path handling, inference
   concurrency and cancellation). Findings and dispositions are appended to
   `docs/verification/windows-voice-flow-2026-10.md`. A PR is not merged with an
   open high-severity Codex finding.
4. **Named-device evidence on the owner's Windows PC.** `scripts/verify_windows_asr.py --audio`
   for Qwen local (T28b), a new `scripts/verify_windows_voice_flow.py` that
   records hotkey → capture → ASR → polish → one real delivery to Open Context
   Flow (event id, receipt, latencies, transcript SHA-256; no content), and the
   WASAPI / hotkey / HUD checks from the 4.2.9 GAP list. Results update the
   4.2.9 GAP successor with device name and commit.

## 6. Delivery plan (PR sequence)

0. **PR-0 Spike.** Frozen Qwen3-ASR-0.6B on Windows CPU, report only. Gates
   the default-route decision for PR-C and the packaging approach for PR-B.
1. **PR-A Rust: send path.** DPAPI key provider, owner-only ACLs, journal
   wiring, Context endpoint default, `/me` + `renew`. Unblocks delivery
   independently of voice.
2. **PR-B Backend sidecar.** `--require-auth` and bundled-mode CORS,
   authenticated health echo, `scripts/build_windows_backend.py`, supervisor
   launch with per-launch token, `backend_base_url` default, settings push
   after health, CI build step.
3. **PR-C Local Qwen3-ASR.** Pinned loading, manifest, download, local-only
   fallback rule, inference lock, `verify_windows_asr.py` support. Matrix entry
   stays "experimental" here.
4. **PR-D Synchronous polish.** `PolishOutcome`, `/v1/voice` `polish` flag and
   `polish_status`, `/v1/text/polish`, tests.
5. **PR-E Composer integration.** Capture result → composer, polish states,
   send-as-typed, Node tests. Depends on A (delivery), B (backend reachable)
   and D (status fields).
6. **PR-F Evidence and docs.** Device evidence scripts and records, matrix
   promotion of `qwen-local` if T28b passes, CI extension, release notes for
   the first Windows candidate.

Each PR carries its Codex audit record. A, B, C and D can proceed in parallel
once PR-0 has reported and A's interfaces are fixed.

## 7. Open points for the owner

- Which Windows PC (name, CPU/GPU, SSH reachability) hosts the device
  evidence; an NVIDIA GPU changes the Qwen local latency story.
- Whether the first Windows candidate defaults to Qwen local or to
  `sherpa-sensevoice` if PR-0 or the device measurement exceeds the 6 s
  threshold.
