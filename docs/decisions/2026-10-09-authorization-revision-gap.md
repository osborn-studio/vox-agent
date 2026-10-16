# Authorization revision is a recorded GAP for 4.2.9 (T03c)

## Status

Accepted as a GAP. The proposal below is ready to implement, but the Windows
4.2.9 preview does not depend on it.

## Date

2026-10-09

## Context

Plan C-18 narrowed a promise. A matching backup receipt proves only what the
server once reported:
- that it accepted the record;
- that it delivered the record, when a message id is present.

It does not prove that the user still holds permission for that target or
project. Today the client learns about lost permission only from:
- a 401 or 403 response;
- a project-catalog refresh;
- a failed session renewal.

Neither the receipt (`eventId`, `status`, `messageId`, `attribution`) nor
`/v1/auth/feishu/me` (`profile`, `targetIds`, expiry) carries anything that
changes when permissions change. Plan task T03c asked to either add such a
revision or record the gap.

The backup server decides access from several sources:
- the on-disk runtime configuration: target membership and tenant-wide target
  ids, activated by a restart;
- the live identity store: user status and organization;
- the project catalog grants.

A counter incremented on every change would have to be bumped correctly in
all of these places. Any path that forgets to bump it would silently weaken
the guarantee.

## Decision

Record the gap for 4.2.9, and keep the narrowed guarantee everywhere:
- `delivered` is described as "the server reported delivery with a message
  ID", never as current permission. This is already enforced by the status
  copy tests and the GAP list.
- Lost permission is detected only through 401/403, a catalog refresh or a
  failed renewal. Each of these cancels the affected lease.

A tripwire test (`tests/test_authz_revision_contract.py`) pins the current
receipt and `/me` field sets. If the server starts sending an authorization
field, the test fails until this decision, the Rust decoder and the client
copy are updated together.

## Proposal for the implementation (when scheduled)

- **Field.** Add `authz` to `/me` and to backup receipts (POST 202 and GET
  200): `"v1:" + hex(SHA-256(...))[:32]`. The hash input is a canonical list
  of every input the access decision reads for this principal:
  - org id;
  - user status;
  - sorted target ids the user may write;
  - whether a tenant-wide target applies;
  - sorted granted project ids;
  - the runtime configuration's content digest.
- **Why a fingerprint.** It is stateless and computed by the same code path
  that authorizes the request, so no mutation path can be forgotten.
- **Rust client.** Keep the last `authz` per principal. A different value
  means:
  - refresh the project catalog and destinations;
  - re-verify pending leases;
  - show "permissions changed; checking".

  It never sends a record again by itself.
- **Compatibility.** The field is optional and additive. The Rust receipt
  decoder already ignores unknown fields; the golden fixtures are regenerated
  from the real service. Contract version: `context-backup-wire` v3.
- **Limits that remain.** A permission change that happens between the
  access check and the response is still not detectable. The fingerprint also
  reveals only that something changed, not what changed.

## Consequences

- The 4.2.9 preview keeps the narrowed guarantee, disclosed in
  `docs/releases/4.2.9-GAP.md`.
- The proposal is concrete enough to schedule as a server task, without
  reopening the design.
