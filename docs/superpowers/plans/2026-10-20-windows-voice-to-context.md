# Windows voice-to-Context flow: master implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Windows Tauri shell deliver the macOS basic flow: hotkey → speak → Qwen3-ASR-0.6B local transcript → Qwen (Bailian) polish → group composer → Send → Context record delivered to the Feishu group "Open Context Flow".

**Architecture:** Keep `apps/desktop-tauri` as the only Windows shell. Bundle a second supervised process, `voxagent-backend.exe` (the frozen `apps/desktop/server.py`), for ASR and synchronous polish behind a per-launch token; unblock delivery with a DPAPI-wrapped encrypted journal and owner-only ACLs; route capture results into the group composer. Spec: `docs/superpowers/specs/2026-10-20-windows-voice-to-context-design.md`.

**Tech Stack:** Rust (Tauri 2, windows-sys, rusqlite, chacha20poly1305), Python 3.12 (FastAPI, httpx, `qwen-asr` + CPU torch, PyInstaller), JavaScript (WebView, Node tests), GitHub-hosted `windows-latest` CI, Codex CLI (`gpt-6-astra`, medium) for audit.

---

## How this plan is organised

This spec spans four subsystems, so it is split into one plan per pull request.
Each sub-plan produces working, testable software on its own and ends with the
Codex audit record. Execute each sub-plan in its own worktree session.

| PR | Sub-plan | Depends on | Produces |
|---|---|---|---|
| PR-0 | [`2026-10-20-win-pr0-qwen-local-spike.md`](2026-10-20-win-pr0-qwen-local-spike.md) | nothing | pinned Qwen manifest, frozen CPU probe on `windows-latest`, decision report |
| PR-A | [`2026-10-20-win-pra-send-path.md`](2026-10-20-win-pra-send-path.md) | nothing | DPAPI key provider, owner-only ACLs, journal wiring, endpoint default, in-process renewal |
| PR-A2 | written after PR-A merges | PR-A | session restore after restart: credential restore, `/me` confirmation via `SessionAuthority::confirm`, destinations and journal reconstruction |
| PR-B | written after PR-0 reports (see "Deferred sub-plans") | PR-0 | `voxagent-backend.exe` sidecar with `--require-auth`, supervisor launch, CI build |
| PR-C | written after PR-0 reports | PR-0 | `QwenLocalASR` pinned loading, local-only fallback, inference lock |
| PR-D | [`2026-10-20-win-prd-sync-polish.md`](2026-10-20-win-prd-sync-polish.md) | nothing | `PolishOutcome`, `/v1/voice` `polish=1` + `polish_status`, `/v1/text/polish`, contracts |
| PR-E | written after PR-B and PR-D merge | PR-A, PR-B, PR-D | capture result → composer, polish states, send-as-typed, Node tests |
| PR-F | written after PR-E merges and the owner names the PC | all | device evidence, matrix promotion, CI extension, release notes |

PR-0, PR-A and PR-D can start now, in parallel, in separate worktrees. They touch
disjoint files:

- PR-0: `engine/model_manifests/`, `scripts/spike_qwen_local_probe.py`, `scripts/qwen_local_probe.spec`, `.github/workflows/qwen-local-spike.yml`, `tests/test_qwen_local_manifest.py`, `docs/verification/`
- PR-A: `apps/desktop-tauri/src-tauri/` (Rust only) and `apps/desktop-tauri/test/` where a JS test asserts `key_store`
- PR-D: `runtime/slowpath/polisher.py`, `apps/desktop/server.py`, `packages/contracts/src/fastpath-result.ts`, `apps/desktop-tauri/src-tauri/src/contracts.rs` (one struct), `tests/`

## Inputs still required from the owner

- Windows PC for PR-F: machine name, CPU/GPU (NVIDIA or not), whether SSH is reachable. Not needed before PR-F.
- Default local ASR route: **decided 2026-10-21 by the owner after the PR-0 report (PR #10): `sherpa-sensevoice` is the default for the first Windows candidate; Qwen3-ASR-0.6B local is opt-in in Settings.** PR-F's measurement on the owner's PC may reopen the default; until then PR-B and PR-C build to this decision.

## Standard Codex audit procedure (every PR)

Run from the PR's worktree root after the branch is pushed and CI is green.

- [ ] **Diff review**

```bash
codex review --model gpt-6-astra -c model_reasoning_effort=medium --base origin/main 2>&1 | tee .codex-review.log
```

Expected: a Markdown list of findings with severities. If the command refuses `--base`, run `git diff origin/main...HEAD > /tmp/pr.diff` and use `codex exec --model gpt-6-astra -c model_reasoning_effort=medium --sandbox read-only "Review this diff as a strict code reviewer; list defects with severity (high/medium/low), file:line and a one-line fix. Diff follows." < /tmp/pr.diff`.

- [ ] **Adversarial pass against the fixed checklist**

```bash
codex exec --model gpt-6-astra -c model_reasoning_effort=medium --sandbox read-only --skip-git-repo-check -o .codex-adversarial.md \
"You are attacking this pull request. Check the working tree against this checklist and report only concrete, reproducible problems with file:line: \
(1) any secret, token or provider key reaching source, argv, logs, event payloads or test fixtures; \
(2) fail-open paths: ASR, polish or backend auth that proceeds when a runtime, model, key or token is missing; \
(3) journal availability: any path where delivery proceeds without an encrypted journal or with an over-permissive directory; \
(4) endpoint defaults: anything that could send data to a non-production or attacker-chosen origin; \
(5) Windows path handling: temp files held open, path separators, symlinks, LOCALAPPDATA assumptions; \
(6) inference concurrency and cancellation: reentrancy, timeouts that do not stop work, temp-file races. \
Finish with PASS or FAIL and the list of high-severity items."
```

Expected: the file ends with `PASS` or `FAIL`.

- [ ] **Record the result**

Append to `docs/verification/windows-voice-flow-2026-10.md` (create on first use with the heading `# Windows voice flow: Codex audit records`):

```markdown
## <PR title> — <branch> — <commit sha>

- Date: <YYYY-MM-DD>
- Model: gpt-6-astra, medium
- Diff review findings: <n> high / <n> medium / <n> low
- Adversarial pass: PASS | FAIL
- Dispositions:
  - <finding> → fixed in <sha> | accepted because <reason> | out of scope: <follow-up>
```

- [ ] **Gate**

A PR is not merged while a high-severity finding is open. Fix, push, rerun the two commands, update the record.

## Definition of done per PR

1. CI green on all eight Verify jobs plus any job the PR adds.
2. Codex audit record appended with no open high-severity finding.
3. PR description lists what was verified locally, what only CI verified, and what still needs the owner's Windows PC.
4. Nothing merged by the executor; the owner merges.

## Deferred sub-plans

PR-A2, PR-B, PR-C, PR-E and PR-F are written by the same process as the three plans
linked above (brainstorm already done; use `superpowers:writing-plans` with the
spec sections 3.1, 3.2, 3.4, 5) once their inputs exist:

- PR-B needs PR-0's measured frozen-binary size and the exact PyInstaller
  `collect_all` list that worked, which become the inputs to
  `scripts/build_windows_backend.py`.
- PR-C needs PR-0's confirmation that `Qwen3ASRModel.from_pretrained(<local dir>)`
  loads from the pinned nine files offline, and the measured warm latency to set
  the default route.
- PR-A2 needs PR-A's refresh-token storage and `JournalOpenReport`, and a read of
  `SessionAuthority::confirm` (`context_session.rs:325`) and the `/me` route.
- PR-E needs the merged `polish_status` contract (PR-D) and a reachable bundled
  backend (PR-B) to write end-to-end Node tests against.
  Owner decision 2026-10-21 (recorded on PR #12): PR-E also decides, in one
  place, what happens to `should_autopaste` and SendInput injection when inline
  polish fails; PR-D deliberately left both unchanged.
- PR-F needs the owner's PC details.

## Spec coverage map

| Spec section | Where it is implemented |
|---|---|
| 3.1 one shell, two processes, authenticated loopback | PR-B |
| 3.2 local ASR pinned loading, local-only fallback, lock | PR-0 (manifest, feasibility), PR-C |
| 3.3 synchronous polish, `polish_status`, `/v1/text/polish` | PR-D |
| 3.4 composer integration | PR-E |
| 3.5 encrypted journal, DPAPI, owner-only ACL | PR-A |
| 3.6 endpoint default, renew while running | PR-A |
| 3.6 restore after restart (`/me` confirmation) | PR-A2 |
| 4 security boundaries | every PR's Codex adversarial pass |
| 5.1 feasibility spike | PR-0 |
| 5.2 CI | PR-B (build step), PR-F (regression + Node tests on Windows) |
| 5.3 Codex audit | this file, every PR |
| 5.4 device evidence | PR-F |
