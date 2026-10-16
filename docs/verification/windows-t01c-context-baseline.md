# T01c — Context / three-pane baseline subset (macOS → Windows)

Source of truth: the installed macOS release **v4.2.8.1, commit `5c9f3a74d3bf438373bac50fb4e0d3dc3d3f5975`**
(the Context sidebar build, which is not on `main`). The facts below are extracted from that commit by
`scripts/t01c_context_baseline.py` into `windows-t01c-context-baseline.json`; `tests/test_t01c_context_baseline.py`
keeps this table in step with the snapshot.

**Scope.** This is the T01c subset only: the default group, the sidebar and the Context API surface. It is
**not the full macOS baseline inventory (T01)**: settings, vocabulary, history, modes, voice and engine
operations are mapped there. Engine routes the Swift client calls (`v1/agents`, `v1/message`,
`v1/permissions/pending`, `v1/tasks`) are out of this subset.

Status words: **PASS** = implemented and verified locally (evidence task in `build/windows-v4.2.8/evidence/`),
**PARTIAL** = core verified, a named part missing, **GAP** = not on Windows yet (named follow-up), **BY DESIGN** =
deliberately different on Windows.

## Context API paths

| macOS path | Windows | Status | Evidence / gap |
|---|---|---|---|
| `v1/input-backups` | `context_wire.rs` submit + read-back, `context_service.rs` | PASS | T04, T05, T10 (team records; dispatch needs DPAPI, T07b) |
| `v1/context-projects` | `context_wire.rs` `fetch_projects`, project menu | PASS | T15 |
| `v1/context-weekly-draft` | `context_sources.rs`, `context_weekly_commands.rs`, weekly panel | PASS | T21, T22, T22-ui |
| `v1/context-private` | `context_private.rs` mutations, `context_private_service.rs` list/get | PARTIAL | T18a, T18b, T19, T20; no private journal until T07b |
| `v1/context-private-search` | `context_private_service.rs` search | PARTIAL | T19; journal waits for T07b |
| `v1/context-private-events` | readback in `context_private.rs` | PARTIAL | T18a/T18b; journal waits for T07b |
| `v1/context-private-reference-check` | `PrivateService::reference_check`, weekly copy gate | PARTIAL | T22; journal waits for T07b |
| `v1/context-private-authorization` | not called | GAP | Windows learns refusal from the private calls themselves; a preflight like macOS's is a follow-up for the private panel |
| `v1/conversations` | `context_account.rs` discovery; `context_conversation.rs` history, text send, receipts | PARTIAL | T24, T25 (Rust core); conversation timeline UI and T27 wiring are a GAP |
| `v1/auth/feishu/begin` | `context_login.rs` | PASS | T24a, T24 |
| `v1/auth/feishu/browser` | `context_login.rs` (browser URL check) | PASS | T24a |
| `v1/auth/feishu/redeem` | `context_login.rs` loopback completion | PASS | T24a |
| `v1/auth/feishu/poll` | not used | BY DESIGN | Windows completes sign-in through the loopback callback and `redeem`, not polling |
| `v1/auth/feishu/logout` | `context_account.rs` `logout` | PASS | T24 |
| `v1/auth/feishu/me` | not called | GAP | session restore after restart (Unverified + online confirm) is not wired (T24 exclusion) |
| `v1/auth/feishu/renew` | not called | GAP | Windows has no refresh-token renewal; an expired session asks to sign in again |
| `v1/auth/feishu/link-previous` | not called | GAP | carrying records of an earlier session of the same account is not implemented |

## API operations built at run time

The path families above are extended at run time with these literal segments
(`dynamicPathSegments` in the snapshot); each operation is mapped here.

| macOS operation | Windows | Status | Evidence / gap |
|---|---|---|---|
| `GET v1/input-backups/{eventId}` | `context_wire.rs` read-back | PASS | T04 |
| `GET` / `POST v1/conversations/{id}/messages` | `context_conversation.rs` `fetch_history` / `post_text` | PARTIAL | T25 core; no conversation UI |
| `GET v1/conversations/{id}/receipts/{eventId}` | `context_conversation.rs` `get_receipt` | PARTIAL | T25 core; no conversation UI |
| `GET` / `PUT v1/context-private/{id}` | `context_private_service.rs` get, `context_private.rs` edit | PARTIAL | T19, T20; journal waits for T07b |
| `POST v1/context-private/{id}/withdraw` | `context_private.rs` withdraw | PARTIAL | T20; journal waits for T07b |
| `GET v1/context-private-events/{eventId}` | `context_private.rs` create readback | PARTIAL | T18a; journal waits for T07b |
| `GET v1/context-private-events/{eventId}/requests/{requestId}` | `context_private.rs` request readback | PARTIAL | T18a; journal waits for T07b |

## Sidebar selections

| macOS `SidebarSelection` | Windows | Status | Evidence / gap |
|---|---|---|---|
| `backupGroup` | "Open Context Flow" default target (`#target-group`) | PASS | T11–T14 |
| `agent(String)` | collapsible agent roster (`#target-coordinator`, `#agent-targets`) | PASS | T11; agent execution needs the engine (T29) |
| `feishu(String)` | conversations listed in the right panel, not selectable as a sidebar target | GAP | conversation timeline + composer (with T27 voice drafts) |

## Sidebar labels

| macOS label (en) | Windows | Status |
|---|---|---|
| Team context capture | Open Context Flow heading and target | PASS |
| Collapse sidebar | `#toggle-sidebar` (Ctrl+Alt+B) | PASS |
| Refresh agents from ~/.osborn/ | roster loaded at start (`get_agents`) | PARTIAL (no refresh control) |
| Refresh local agents | as above | PARTIAL (no refresh control) |
| Workspace | "Workspace" section | PASS |
| Groups | Open Context Flow under "Conversations" | PASS |
| Optional Agents | collapsible "Agents" section | PASS |
| Refresh conversations | `#context-refresh-conversations` (right panel) | PASS |
| Other Feishu conversations | conversation list in the right panel | PARTIAL (not a sidebar target) |
| Settings | Settings view | PASS |
| Open settings | Settings view button | PASS |
| Vocabulary | Vocabulary view | PASS |
| Open vocabulary | Vocabulary view button | PASS |
| Sync (Notion first, Git when disabled) | `sync_notion_history` / `sync_git_history` commands | GAP (no sidebar control; mapped in T38) |
| Sync local records | as above | GAP |
| Syncing | as above | GAP |
| Syncs through Notion or Git; this is not the Feishu input backup control. | as above | GAP |
| Online update | updater | GAP (T42; manual download in the preview) |
| Update available | as above | GAP |
| Feishu group · Local submissions | scope line "local inputs and delivery status · not the group's full history" | PASS |
| Local submissions for the Feishu group; sending follows your backup settings. | same scope line and delivery section | PASS |
| Selected | `aria-pressed` on the active target | PASS |
| Session expired. Sign in again. | account error copy `sign-in-required` | PASS |
| Permission missing or revoked. | `not-permitted` | PASS |
| This server version does not support conversation discovery. | generic `conversations-unavailable` text only | PARTIAL (no separate diagnosis) |
| Full Feishu discovery is not enabled. | generic `conversations-unavailable` text only | PARTIAL (no separate diagnosis) |
| Service unavailable or invalid response. Retry. | `invalid-response` / `network` | PASS |
| Sign in with Feishu first. | `not-signed-in` | PASS |
| Some group access was revoked. Other conversations are unaffected. | discovery refresh drops revoked entries | PARTIAL (no dedicated notice) |
| Discovering conversations… | conversations note while loading | PASS |
| No shared groups. | empty conversation list | PARTIAL (no dedicated text) |
| Discovery only. Messaging is not enabled. | `view only` capability word | PASS |
| Personal channel mirror · No replies | personal conversation kind word | PARTIAL |
| Discovery only | `view only` | PASS |
| Bot not configured | — | GAP (agent bindings are not in the preview) |
| \(count) msgs | — | GAP (agent message counts) |
| Bound | — | GAP (agent bindings) |
| Delete \(agent.name) | — | GAP (agent management) |
| \(summary.failedCount) failed | status strip `failed` segment | PASS |
| \(summary.queuedCount) queued | status strip `queued` segment | PASS |
| \(summary.awaitingApprovalCount) awaiting approval | status strip `awaiting approval` segment | PASS |
