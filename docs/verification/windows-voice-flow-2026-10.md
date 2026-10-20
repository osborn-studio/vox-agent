# Windows voice flow: Codex audit records

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
