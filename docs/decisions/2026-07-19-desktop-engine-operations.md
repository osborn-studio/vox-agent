# Desktop Agent Operations Control Plane

- Status: Accepted
- Date: 2026-07-19
- Scope: `apps/desktop-tauri`, Python Agent Engine

## Context

The desktop shell historically sent voice-memory and grounded-answer requests to
the gateway on port `7901`. That path is useful for VoxLog-compatible capture and
recall, but it does not expose Vox Agent's scheduler, task lifecycle, specialist
agents, tool approvals, or real-time execution events. A Cockpit that only
rearranges the legacy gateway data is therefore not a Vox Agent upgrade.

The Python Agent Engine already owns the canonical task APIs, the permission
manager, and the authenticated `/ws` event stream on loopback port `18900`.

## Decision

1. The desktop Cockpit uses the Python Agent Engine as its Agent operations
   control plane. Typed commands go through `/v1/message`; task, agent, and
   pending-approval snapshots come from engine APIs; execution changes arrive
   through `/ws`.
2. The legacy gateway remains the voice-memory bridge. Its URL and credential
   are not reused as an implicit Agent control-plane contract.
3. Rust owns the engine HTTP/WebSocket connection. The engine URL is fixed to
   `127.0.0.1:18900`, and an optional engine admin token is read from the OS
   credential store. Stored credential values and Authorization headers are
   never returned to the WebView.
4. Rust forwards only parsed JSON event envelopes to the WebView over a named
   Tauri event. The WebView renders task state and approval scope as text; it
   never evaluates event payloads as HTML.
5. Approval decisions return through the existing admin-only
   `/v1/permissions/approve` route. The server derives the actor from the
   authenticated principal and records the decision in the audit log.
6. The engine exposes an admin-only pending-approval snapshot so a reconnecting
   Cockpit does not silently lose an in-flight request. WebSocket events remain
   the low-latency path; snapshots are the reconciliation path.
7. The Cockpit may auto-start the bundled local engine after a failed health
   probe. It must show an unavailable state if startup or authentication fails;
   it must not silently fall back to presenting a grounded VoxLog lookup as an
   executed Agent task.

## Event contract

The Rust bridge forwards the existing engine envelope:

```json
{
  "type": "task:progress",
  "payload": {
    "task_id": "task-123",
    "status": "running"
  }
}
```

The Cockpit reconciles its snapshot after task lifecycle, agent status, and
permission events. Unknown event types are logged and ignored by the UI.

## Security invariants

- Engine credentials are stored under a distinct `engine_admin_token` keyring
  entry and are never persisted in desktop settings JSON or browser storage.
- The engine bridge cannot be redirected to a remote host, preventing bearer
  credential exfiltration through a user-controlled URL.
- Enterprise WebSocket and permission routes remain admin-only and fail closed.
- Approval scope is inserted with DOM text APIs, not `innerHTML`.
- The UI cannot claim success until the authenticated server accepts a decision.

## Verification

- Python tests cover pending-approval reconciliation, admin authorization, and
  actor-bearing decision events.
- Rust tests cover fixed-loopback request construction, token attachment, and
  snapshot/event contract decoding.
- Desktop contract tests cover Tauri argument casing, event registration,
  dynamic task/approval surfaces, and the absence of credential handling in
  JavaScript.
- macOS and Windows Tauri builds remain required merge gates.
