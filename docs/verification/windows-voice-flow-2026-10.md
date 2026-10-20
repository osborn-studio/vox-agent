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
- CI: `windows-tauri-test` green on c89fd7e, dceac76 and b777eab; the run for fe723ce (adds the dispatch-time ACL check, junction tests and child checks) was in progress when this record was written and its result is in the PR checks
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
