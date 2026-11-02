---
name: verify-vox-agent
description: Drive the real Vox Agent product from this checkout and prove behavior with evidence. Vox Agent is a voice-first agent cockpit; on Linux the drivable surfaces are the Python engine (FastAPI, `vox-engine` CLI, 127.0.0.1:18900) and the React web console (apps/web-console, Vite :5273). Use when you need to launch an isolated engine (+ console), run the capture → transcript → memory → agent-query loop like a user with the deterministic mock ASR/answerer, screenshot console tabs, and keep proof after teardown — e.g. before a PR touching engine/api, capture, memory, devices, agents, or the web console.
---

# Verify Vox Agent

Vox Agent (`pyproject` name `vox-agents`; legacy internal name `VoxLog`) is a voice-first agent cockpit. Surfaces, in order of what this skill can drive on a Linux box:

1. **Engine HTTP API + `vox-engine` CLI** — `engine/server.py` (FastAPI, 150+ routes) started by `.venv/bin/vox-engine start`. The CLI client commands (`status`, `device`, `capture`, `memory`, `tokens`, `tasks`, ...) are HTTP calls to a hard-coded `http://127.0.0.1:18900`. Primary surface here.
2. **Web console** — `apps/web-console` (Vite + React), tabs `Sessions | Memory | Agents | Devices | Admin`. Its API client is hard-coded to `http://127.0.0.1:18900` (`src/api/client.ts`).

Not covered (cannot run on this box or needs credentials/hardware): native macOS cockpit (`macos/`, SwiftUI — needs macOS), Tauri desktop shell (`apps/desktop-tauri` — needs a display + Rust/WebView toolchain; `npm run dev:phase0` also needs `apps.desktop.server`), mobile companion (Expo, devices), the TS gateway `services/api-ts` legacy voice routes, real microphone capture, the `sherpa` local ASR adapter (needs `pip install '.[asr]'` + downloaded SenseVoice/VAD models), the `llm` answerer and any paid provider (DashScope etc.), enterprise edition auth, Feishu/Obsidian integrations, `vox-engine task` (spawns Claude/Codex CLIs), deploy scripts. `run.sh`/`start.sh` start a legacy `server.app:app` that no longer exists in this tree — do not use them.

**Isolation rule:** the engine port 18900 is hard-coded in the CLI and console, so only ONE verify instance per host. `launch` refuses to start if anything already listens on 18900 — never drive an engine you did not launch (it may be the user's, with real `~/.voxagent` data). Every run gets its own scratch `HOME`, `VOXAGENT_DATA_DIR`, SQLite voice-memory DB and audio dir; nothing touches `~/.voxagent` or `~/.voxlog2`.

Helpers (executable, in `scripts/`):

- `scripts/verify-vox-agent.sh` — `setup | launch [--console] | doctor | env | cli | api | wav | db | ui | keep | cleanup`. All state is keyed by `RUN_ID`.
- `scripts/console-drive.mjs` — headless Chrome over CDP: open the console, run a JSON list of steps (click button by text, fill input by placeholder, submit form, wait for text, intermediate shots), write PNG + DOM + innerText + the list of engine requests the page made. Called by `ui`; usable directly.

Set once per run (from the repo root):

```bash
export RUN_ID=verify-$(date +%Y%m%d-%H%M%S)
V=.cursor/skills/verify-vox-agent/scripts/verify-vox-agent.sh
```

Prerequisites: Python ≥ 3.11 (`python3 -m venv`), Node ≥ 22 (global `fetch`/`WebSocket` for the CDP driver; proven with Node 24), Chrome/Chromium (`CHROME_BIN` overrides), `curl`, `ss`, `setsid`. No API keys, models, microphone or network services are needed for the mapped journeys.

## Launch

```bash
$V setup              # once per checkout change: .venv + pip install -e '.[dev,mcp]', npm ci, build packages/contracts (~1-2 min)
$V launch --console   # prints: ready: engine pid=<pid> url=http://127.0.0.1:18900 data=<scratch>/data console=http://127.0.0.1:5273 evidence=<dir>
```

Omit `--console` for CLI/API-only journeys. `launch` creates `/tmp/vox-agent-verify/$RUN_ID/{home,data}` (empty), starts `vox-engine start --port 18900` under `setsid` with `HOME`, `VOXAGENT_DATA_DIR`, `VOXAGENT_VOICE_MEMORY_DB`, `VOXAGENT_AUDIO_DIR` pointed into scratch, `VOXAGENT_EDITION=personal`, `VOXAGENT_ASR_ADAPTER=mock`, `VOXAGENT_ANSWERER=mock`. The engine migrates every SQLite DB on first open; no seed data is required. Ready means all of: the log has `engine ready: 8 tools, 0 skills, 0 plugins, 0 schedules` and `Application startup complete`, `/health` answers, and the principal-scoped `/v1/capture/sessions` returns 200. With `--console`, Vite serves `apps/web-console` on 127.0.0.1:5273 (an origin in the engine's default CORS allowlist; `VOX_VERIFY_CONSOLE_PORT=5173` is the only other allowed default).

Expected harmless warnings in the engine log: `config: ~/.osborn/config/agents/ does not exist`, `config: no agent CLIs detected (claude, codex)`.

Teardown is `$V cleanup`.

## Doctor

```bash
$V doctor
```

Read-only. Checks: the recorded engine pid is alive and its cmdline is this checkout's `.venv/bin/vox-engine start`; port 18900 is owned by that pid; the pid's environment has the scratch `VOXAGENT_DATA_DIR`; `/health` is `{"status":"ok",...}`; the served OpenAPI `info.version` equals this checkout's `pyproject.toml` version (catches a stale engine from another checkout); the personal principal is accepted on `/v1/capture/sessions`; the scratch `voice_memory.db` exists; and, if launched with `--console`, the Vite pid is alive and `/` serves `<title>VOXAGENT Console</title>`. Ends with `doctor: healthy (RUN_ID=...)` or `doctor: NOT healthy — do not drive` (exit 1). Run it first and whenever output looks odd. After code changes, `cleanup` + `launch` again (uvicorn runs without reload).

## Drive

Recipes per feature live in [`features/`](features/README.md). Shared mechanics:

- **CLI:** `$V cli <label> -- <vox-engine args...>` runs the checkout's CLI with the run's env and writes `cli/<label>.{cmd,stdout,stderr,exit}`; prints stdout, returns the CLI exit code. Client commands print JSON unless `--pretty`; parse JSON, e.g. `| python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'`.
- **HTTP:** `$V api <label> <METHOD> <path> [json-body]` → `api/<label>.{req,json,status}`; exit 1 on non-2xx. Use it only for routes with no CLI or console entry (e.g. `POST /v1/agents/query` from a script) or for a read-only second view.
- **Audio fixture:** `$V wav <name> [seconds]` writes a synthetic 16 kHz mono 440 Hz tone into scratch and prints its path. The repo ships no recorded speech (omitted from the source snapshot); `scripts/generate_synthetic_test_audio.py --output <dir>` makes a non-speech triangle wave too.
- **Side effects:** `$V db <label> "<sql>"` runs read-only SQL over scratch `voice_memory.db` (tables `capture_sessions(id,created_at,data)`, `transcript_segments(id,session_id,seq,data)`, `memory_nodes(id,node_type,source_session_id,created_at,data)`, `session_audio(session_id,path,created_at)`; `data` is JSON — use `json_extract(data,'$.status')`) with `admin_audit.db` attached as `audit` (`audit.admin_audit_event(id,action,actor_id,workspace_id,target_id,metadata,created_at)`). Writes `db/<label>.{sql,txt}`.
- **Console:** `$V ui <label> '<steps-json>'` (needs `launch --console`). Step forms: `{"click":"<button text or prefix>"}`, `{"fill":{"placeholder":"<exact placeholder>","value":"..."}}`, `{"submit":"<placeholder of an input in the form>"}`, `{"waitText":"..."}`, `{"waitNoText":"..."}`, `{"shot":"<sub-label>"}`. Stable handles (no data-testids exist): tab buttons `Sessions`/`Memory`/`Agents`/`Devices`/`Admin` in `.topbar .tabs`; placeholders `Search memory (e.g. Japan launch)`, `Ask a question about your memory…` (Unicode ellipsis), `Pairing code`, `Owner user id`, `Workspace id`; buttons `Search`, `Ask`, `Generate pairing code`, `Pair device`, `Recent`, `Action items`. Writes `ui/<label>.{png,text.txt,dom.html,network.json}`; exit 1 if a step times out (15 s each).
- **Keep scratch artifacts:** `$V keep <label> <path>` copies into `kept/<label>/` before cleanup (e.g. the uploaded WAV under `<scratch>/data/audio/`).

## Evidence

Location: `${VOX_VERIFY_EVIDENCE:-$HOME/vox-agent-verify-evidence}/$RUN_ID/` — outside the repo and outside scratch, so cleanup never removes it and git never sees it.

```
engine-launch.log engine-final.log console-final.log
cli/<label>.cmd|.stdout|.stderr|.exit
api/<label>.req|.json|.status
db/<label>.sql|.txt
ui/<label>.png|.text.txt|.dom.html|.network.json   (+ ui/<label>.<shot>.png|.text.txt)
kept/<label>/...
```

Proof standards:

- Exercise the real user path: the `vox-engine` CLI and the console in a browser. Do not call Python functions, `TestClient`, or `engine.mock_asr.seed_workflow` as proof, and do not insert SQLite rows.
- Capture the action and the result: the CLI command + exit code, then a second read-only view (`$V db`, a GET, or a console screenshot) of the new state.
- Verify side effects: rows in `capture_sessions`/`transcript_segments`/`memory_nodes`, the uploaded file in `session_audio` + scratch audio dir, and the matching `admin_audit_event` actions (`device.paired`, `capture.session.created`, `capture.audio.uploaded`, `agent.query`).
- **Console mock fallback:** every console read silently falls back to built-in mock data when the engine is unreachable or returns non-2xx (`MOCK_SESSIONS` title `Japan launch planning`, ids `cap_mock_*`, devices `device_mock`). A screenshot alone is never proof. Check `ui/<label>.network.json` (every non-`OPTIONS` engine request 2xx, no `error`; CORS preflight `OPTIONS` rows carry `status: null` and are fine) and assert on ids you created in this run (`cap_<12 hex>`, `mem_<12 hex>`, `dev_<12 hex>`).
- **Mocks in play:** `VOXAGENT_ASR_ADAPTER=mock` and `VOXAGENT_ANSWERER=mock` are the product's own default adapters (selected by env/model policy at a real production boundary), not test doubles. The mock ASR ignores the audio content and always yields the same 7-segment zh/ja/en "Japan launch" meeting; the mock answerer concatenates cited memory. Say so when reporting: speech recognition accuracy and LLM answers are NOT verified by these journeys.
- Report a journey that cannot run with the exact command, exit code, and unmet precondition. Never weaken a journey to make it pass.
- Never put tokens in evidence. Personal edition needs none; if you ever test enterprise edition, pass the bearer token via env, not argv, and scan evidence (`grep -rIE 'Bearer [A-Za-z0-9]|token' <dir>`) before sharing.

## Cleanup

```bash
$V cleanup
```

Stops only the pids recorded in `/tmp/vox-agent-verify/$RUN_ID/{console,engine}.pid` after confirming each cmdline still matches what `launch` started (`vite.js` / this checkout's `vox-engine start`), signalling the `setsid` process group (never by name). Copies final logs into evidence, then deletes `/tmp/vox-agent-verify/$RUN_ID` (scratch HOME, DBs, audio). Prints `evidence kept: <dir>`; confirm with `ls <dir>` and `ss -ltn | grep -E ':18900|:5273'` (expect nothing). `console-drive.mjs` kills its own Chrome and deletes its temp profile. Run cleanup after every failed attempt too; `launch` refuses to reuse an existing scratch dir.

## Helpers

```bash
.cursor/skills/verify-vox-agent/scripts/verify-vox-agent.sh          # usage
node .cursor/skills/verify-vox-agent/scripts/console-drive.mjs <url> <out-prefix> '<steps-json>' [--timeout-ms N]
```

Env knobs: `RUN_ID` (default `default`), `VOX_VERIFY_SCRATCH` (default `/tmp/vox-agent-verify`), `VOX_VERIFY_EVIDENCE` (default `$HOME/vox-agent-verify-evidence`), `VOX_VERIFY_CONSOLE_PORT` (default `5273`), `CHROME_BIN`.

Known traps (seen when this skill was proven on a Linux box):

- CSS `text-transform: uppercase` makes section titles uppercase in `innerText` (`SEARCH RESULTS FOR "JAPAN LAUNCH"`); `waitText` therefore also matches `textContent`, but grep `ui/*.text.txt` case-insensitively for titles.
- `vox-engine capture process` is idempotent: if a session already has segments it skips ASR entirely, so `--asr sherpa` on a processed session returns success without touching sherpa. Use a fresh session to test an adapter.
- `--asr sherpa` without the optional runtime/models returns HTTP 500 (`sherpa-onnx model not found at <data>/models/...` in the engine log), surfaced by the CLI as `Process failed: HTTP 500: Internal Server Error`.

Maintenance: keep `features/` honest as the app changes with `/maintain-verification-skill`.
