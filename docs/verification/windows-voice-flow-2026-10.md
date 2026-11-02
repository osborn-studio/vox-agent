# Windows voice flow: Codex audit records

Procedure: `docs/superpowers/plans/2026-10-20-windows-voice-to-context.md`,
"Standard Codex audit procedure". One record per pull request; a record is
rerun and amended after every fix until no high-severity finding is open.

## PR-A: Windows send path (DPAPI journal, ACL, endpoint, renew) — claude/elastic-dijkstra-944d46 — fe723ce

- Date: 2026-10-20
- Model: gpt-6-astra, medium (`codex review -c model="gpt-6-astra" -c model_reasoning_effort=medium --base origin/main`; codex-cli 0.160.1 refuses `--model` on `review`, the config override is the equivalent)
- Rounds: six diff reviews and five adversarial passes, each rerun after the fixes it caused; the final diff review (fe723ce) reports no actionable regression. An independent Rust review (Claude subagent) ran before the first Codex round; its findings are folded into the same dispositions.
- Diff review findings (all rounds, deduplicated): 3 high / 6 medium / 1 low from Codex, plus 3 high / 7 medium / 8 low from the independent review
- Adversarial pass (final, fe723ce tree): FAIL on two **pre-existing** high-severity items outside this PR (see dispositions); no in-scope high-severity item open
- CI: all eight Verify jobs green on f6ddedb (fe723ce plus this record), run 39462919227, including `windows-tauri-test` with the new `cargo check --release --locked` step; `windows-tauri-test` was also green on c89fd7e, dceac76 and b777eab
- Dispositions:
  - `FileAnchorStore` is `cfg(any(test, debug_assertions))`, so the Windows release build could not link the journal path (independent review, high) → fixed in b777eab: anchors go to the credential store (`NativeSecretStore: AnchorStore`), tests inject the file store through `open_journal_with`; `cargo check --release --locked` added to `windows-tauri-test`
  - A set but non-Unicode `VOX_AGENT_CONTEXT_ENDPOINT` fell back to production (independent review, high) → fixed in b777eab: `context_endpoint_from_var` treats `VarError::NotUnicode` as an invalid override (sign-in disabled)
  - `ensure_private_dir` could leave an empty inherited directory behind after a failure, and `create_dir_all` could adopt a directory created meanwhile (independent review, medium) → fixed in b777eab: SID and descriptor are built first, only the leaf is created, `AlreadyExists` verifies, a leaf this call created but could not secure is removed
  - Owner not checked; reparse points followed; ACE SID addressed through a 12-byte struct reference; DPAPI plaintext buffer freed without zeroing (independent review, medium/low) → fixed in b777eab
  - Renewal: refresh-token store failure after a rotation retried every minute; refresh token read without a lease check; `expires_in == None` with a refresh token offered scheduled nothing; `Instant` stops during sleep; reschedule and sign-out side effects not re-checked against the current account; no WebView notification on a server-side sign-out (independent review, medium/low) → fixed in b777eab and 956f329
  - Journal lock held by a sleeping record driver across sign-out/sign-in, so the next sign-in got `Busy` and no journal (Codex round 1, P1) → fixed in 956f329: drivers hold a `Weak` service across sleeps; sign-in retries `Busy` six times at five-second intervals
  - Renewal sign-out side effects not atomic with the account check (Codex round 1, P2) → fixed in 956f329: `end_if` returns whether it ended the account; the schedule is keyed by service identity
  - `journal\db` not ACL-checked (Codex adversarial round 1, high) → fixed in 956f329/b0b9324: `db\` and `isolated\` are verified as children before and after the open
  - Pre-creating `journal\db` made `Journal::open` archive it as orphaned and recreate it, so the second open was refused (Codex round 2, P1 and adversarial high) → fixed in b0b9324: the journal creates them; child verification accepts inherited owner-only ACEs
  - `SE_DACL_PROTECTED` not checked, so a top-level directory with inheritance enabled could be widened from its parent (Codex round 2, P2) → fixed in b0b9324, with a `/inheritance:e` test
  - WebView cleared only the private panel on an automatic sign-out (Codex round 3, P1) → fixed in 8d106ff: `resetView()` as for an explicit sign-out
  - Windows test asserted `journal\isolated`, which only an archived database creates (Codex round 3, P1) → fixed in 8d106ff
  - Delivery continued after a Context directory was widened during a session (Codex adversarial rounds 3 and 4, high) → fixed in 8d106ff: every reservation and record step re-verifies the directories while a DPAPI journal is open; a widened one sets `key_store = unsafe-acl` and stops delivery
  - Unit assertion on `journal_dirs_private` not platform-gated (Codex round 4, P1) → fixed in 2e3c5ec
  - `due_renewal` could clear a newer account's schedule when a sign-in overlapped a tick (Codex round 5, P2) → fixed in fe723ce: only the inspected schedule is dropped
  - `set_backend_base_url` / saved settings accept any origin and the bridge sends the stored bearer, provider keys and audio there; `upload_voice` sends audio without a token when none is configured (Codex adversarial, every round, high, **pre-existing on `origin/main`**, `commands.rs` / `bridge.rs`) → out of scope: the voice bridge is not part of the Context send path or this plan; follow-up proposed to the owner as its own task
  - Cancelling transcription does not cancel the pending `transcribe()` request; the voice bridge client has no timeout (Codex adversarial, medium, pre-existing, `conversation-draft.js` / `bridge.rs`) → out of scope: same follow-up
  - Release builds honour `VOX_AGENT_CONTEXT_ENDPOINT` (Codex adversarial, medium) → accepted because the spec (§3.6) makes the variable the development override by design; it is limited to https origins and loopback http, and an invalid value disables sign-in rather than falling back
  - Renewal every minute when the server reports an access lifetime below the five-minute margin; blocking credential-store calls on the Tokio runtime; `journal_label` not reset on sign-out (independent review, low) → accepted: bounded by the server's per-user rate limit, consistent with the existing sign-out command, and informational respectively
- Gate: no in-scope high-severity finding open. Not merged by the executor.

"Standard Codex audit procedure". One record per pull request; a PR is not
merged while a high-severity finding is open.

## PR-0: Qwen3-ASR-0.6B frozen CPU feasibility spike — claude/youthful-ishizaka-e565fc — 286910d

- Date: 2026-10-20
- Model: gpt-6-astra, medium (Codex CLI 0.160.1; `codex review` takes the
  model as `-c model="gpt-6-astra"`, not `--model`)
- Diff review findings (first pass at 47411ba): 0 high / 0 medium / 0 low
  ("No actionable defects were identified in the standalone probe, workflow,
  manifest, or supporting tests"; Codex could not run pytest because its shell
  resolved a Python without pytest, so the test runs below are ours)
- Adversarial pass (first pass at 47411ba): FAIL, 0 high / 2 medium
- Dispositions:
  - `scripts/spike_qwen_local_probe.py` `load_manifest` accepted an empty
    `files` list, so `--verify-only` exited 0 with `files_ok: true` against a
    missing model directory → fixed in 8baf127 (empty list raises
    `SystemExit`; `test_load_manifest_rejects_an_empty_file_list`)
  - `download_missing` wrote every download to the shared `<name>.part` and
    renamed it, so two probes in one model directory could truncate or consume
    each other's partial file → fixed in 8baf127 (per-process
    `<name>.<pid>-<random>.part`, removed in `finally`;
    `test_download_uses_a_private_temp_file_and_leaves_none_behind`)
- Diff review findings (rerun at 8baf127): 0 high / 1 medium (P2) / 0 low
- Adversarial pass (rerun at 8baf127): PASS, 0 high
- Dispositions (second pass):
  - `load_model` chose CUDA when `torch.cuda.is_available()`, so the owner's
    NVIDIA PC would measure the GPU route and `build_report` would mark the run
    `success: false` → fixed in fc00277 (`device_map="cpu"` always; the
    reported device comes from the loaded model;
    `test_load_model_forces_cpu_even_when_cuda_is_available`)
- Diff review findings (final at fc00277): 0 high / 0 medium / 0 low ("No actionable defects were identified"; Codex ran the 14 focused tests itself this time)
- Adversarial pass (third pass at fc00277): FAIL, 0 high / 1 medium (P2)
- Dispositions (third pass):
  - `download_missing` replaced the destination before checking the bytes, so
    a corrupt or short download from one run could overwrite a file another
    run had already verified in a shared directory → fixed in 286910d (size
    and SHA-256 are checked on the private temp file; a mismatch is reported
    and the destination is left alone; `test_download_verifies_before_publishing`)
- Diff review findings (final at 286910d): 0 high / 0 medium / 0 low
- Adversarial pass (final at 286910d): PASS, 0 high
- Gate: nothing open at any severity at 286910d; this record is the only
  change after that commit. The executor does not merge; the owner merges.

## PR-D: Synchronous polish with explicit polish_status — claude/dreamy-yonath-ce8672 — b5c8d5a5d32dc779a76795b315f8eace8ba8412a

- Date: 2026-10-20
- Model: gpt-6-astra, medium
- PR: https://github.com/osborn-studio/vox-agent/pull/12
- Commands: `codex review -c model="gpt-6-astra" -c model_reasoning_effort="medium" --base origin/main`
  (Codex CLI 0.160.1 rejects `--model` on `review`; the config override is the same model and effort),
  then the adversarial `codex exec` prompt from the master plan with the PR file list.
- Diff review findings (first run, 18e4ef6): 0 high / 1 medium (P2) / 0 low
- Diff review findings (rerun, b5c8d5a): 0 high / 0 medium / 0 low ("no actionable regressions")
- Adversarial pass: FAIL (three items, dispositions below; none left open)
- Dispositions:
  - P2 `apps/desktop/server.py` `/v1/text/polish` stringified a null/bool/list/object `text` into
    draft content → fixed in b5c8d5a (422 for non-string text; test extended).
  - High "new polish endpoint inherits fail-open authentication when `api_token` is empty"
    (`apps/desktop/server.py` `verify_token`; gateway `/v1/text/polish` forwards without caller
    auth) → accepted because it is the existing backend/gateway trust boundary, not a change in
    this PR: every legacy gateway route (`/v1/voice`, `/v1/voice/text`, `/v1/recent/*`) forwards
    with the gateway's backend token and no caller auth, `/v1/voice/text` already triggers
    slow-path provider calls the same way, and `verify_token` is the documented backend model
    (`backend_auth_required` in `/health`). Authenticated loopback (`--require-auth` sidecar) is
    PR-B's deliverable per the master plan §3.1 mapping. The new endpoint uses the same
    `Depends(verify_token)` as every other mutation; `tests/test_desktop_sync_polish.py`
    asserts 401 when a token is configured.
  - High "requested polish failure still returns `status: ok`, `should_autopaste: true`,
    `needs_review: false`" → accepted because spec §3.3 says clients key on `polish_status`
    and §3.4 defines the client behaviour on failure (draft placed in the composer with an
    inline note and a "直接发送原文" action; no auto-send). `should_autopaste`/`needs_review`
    describe target risk and STT confidence, and changing them for a polish miss would alter the
    macOS and Tauri delivery contracts outside this PR's scope. Owner decision requested in the
    PR description: if private/ephemeral or polish-failed captures should block autopaste, that
    is a small follow-up on `_apply_polish` plus a contract note.
  - High "`runtime/fastpath/stt.py:190` cancellation/temp-file race in `LocalWhisperCpp.transcribe`"
    → out of scope: pre-existing, not touched by this PR (Codex marked it "outside this PR's
    diff"); follow-up for the PR-C local ASR work (inference lock, cancellation that stops work,
    temp-file lifetime).
