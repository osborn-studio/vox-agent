# macOS Cockpit Uses Server-Authoritative Agent Operations

- Status: Accepted
- Date: 2026-07-19
- Scope: shipping SwiftUI application, Python Agent Engine

## Context

The macOS application already presented a three-pane Agent Cockpit, but its
primary task source was a local `tasks.json` compatibility cache. Typed commands
called `/v1/message` with ad hoc JSON, discarded the typed response, and did not
reconcile the server task identifier. `task:created` events were intentionally
ignored, while pending approvals existed only if the WebSocket event arrived
during the current process lifetime.

This made the shipping UI look like Vox Agent while behaving like a decorated
VoxLog history client. It also allowed the local cache and the engine database to
report different task lifecycles.

## Decision

1. While the local Agent Engine is running, `/v1/agents`, `/v1/tasks`, and
   `/v1/permissions/pending` are the authoritative reconciliation snapshot.
2. The Swift `TaskStore` remains a compatibility cache for offline display, but a
   successful engine snapshot replaces its contents with canonical task IDs and
   lifecycle state.
3. Typed commands use one `EngineOperationsClient` and `/v1/message`. The typed
   response is rendered in the conversation and its `task_id` remains attached to
   the Agent turn.
4. WebSocket events are the low-latency invalidation signal. The Cockpit refreshes
   after an event and every five seconds as a missed-event fallback.
5. Pending approvals are rebuilt from the admin snapshot after reconnect. Resolved
   decisions remain in the local timeline as audit history.
6. Agent identity in the sidebar comes from the engine scanner when available.
   Workspace/local agents are used only before the engine becomes ready.
7. The obsolete Swift-to-server WebSocket task request is removed. The WebSocket
   remains server-to-client; commands and decisions use HTTP.
8. `source_agent` is persisted in the canonical task schema so a reconciled task
   does not lose its originating Agent.

## Failure behavior

- A failed snapshot does not delete the last successful compatibility cache.
- An unavailable engine leaves the typed message in voice history but reports the
  offline condition instead of manufacturing a successful Agent result.
- Invalid or non-2xx responses are surfaced as typed failures.

## Verification

- Swift tests cover DTO decoding, canonical task replacement, pending-approval
  reconnect, combined snapshot fetch, and `/v1/message` request construction.
- Python tests cover the `source_agent` schema migration and round trip.
- The release DMG must include the Python WebSocket runtime and the rebuilt SwiftUI
  executable before the next GitHub release is published.
