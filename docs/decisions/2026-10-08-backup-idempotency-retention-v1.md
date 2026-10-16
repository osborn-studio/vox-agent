# Backup idempotency records are durable and never deleted by service code

## Status

Accepted (owner decision D2, 2026-10-08)

## Date

2026-10-08

## Context

Desktop clients submit team Context records with `POST /v1/input-backups`.
A client can crash, or lose the response, at any point around that request.
To avoid both data loss and duplicate Feishu messages it must know whether it
may send the same request again.

The server already behaves idempotently. `BackupStore.replay()` looks up
`(org, actor, eventId)`. If the stored canonical payload fingerprint matches,
the route returns the stored receipt with `202`. If it differs, the route
returns `409 EVENT_PAYLOAD_CONFLICT`. `targetId`, `text`, `projectId`,
`repoSource` and `repository` are part of the fingerprint, not the key.

What was missing was a written guarantee that these records outlive any
client retry window. Without it, the Windows design (§6.2) had to treat every
crash after the local reservation as "uncertain forever" and require a person
to reconcile it.

Verified on 2026-10-08: no code under `engine/`, `scripts/` or `deploy/`
deletes from `backup_records` or `backup_outbox`, drops those tables, or
truncates them. The generic `RetentionSweeper` does not touch the backup
database.

## Decision

1. The idempotency namespace is `(org, actor, eventId)`. It is not scoped by
   target, project or credential.
2. Backup records and their outbox rows are **never deleted** by service code.
   Replay of the same payload returns the stored receipt, including terminal
   states, for the life of the database.
3. Any future retention, purge or archival of these records is a **wire
   contract change**. It needs a new ADR, a bumped `idempotency.retention`
   value and fixture version in
   `apps/desktop-tauri/test/fixtures/context-backup-wire-v2.json`, and client
   support for the new version before it ships. Clients must disable
   automatic resend when they see an unknown retention value.
4. Clients may therefore resend automatically only in this case:
   - `uncertain.pre_dispatch`: the reservation is durable, and the client has
     no evidence that any request body left the process (no HTTP attempt, or
     the attempt failed before the connection was established). The client
     resends the **same eventId with the byte-identical canonical payload**.
     `202` resumes normal tracking. `409` moves the record to `conflict`.
5. Clients must not resend automatically in this case:
   - `uncertain.post_dispatch`: the request body may have been sent and the
     response was lost. The client only reads back with
     `GET /v1/input-backups/{eventId}`. `404` there does not prove the request
     was never received.
6. Resending a different payload under an existing eventId is never allowed.
   A user edit creates a new eventId.

## Snapshot restore

Durability holds within one database lineage. An operator disaster-recovery
restore of an older snapshot removes records accepted after that snapshot.
This does not make automatic resend unsafe, because rule 4 lets a client
resend only when no request bytes left the client, so the server never held
those records. `post_dispatch` records stay GET-only; after a restore they
read back as `404` and remain uncertain for a person to reconcile. Operators
follow `deploy/feishu-team/ACCESS.md`: keep submissions disabled
(`503 BACKUP_DISABLED`) until the restored database is reconciled, and never
restore over a live database without that review.

## Consequences

- The Windows journal can close the "reserved but never sent" crash window
  without human reconciliation (task T09).
- The server database grows with every record. Storage growth is an operator
  concern and is handled by capacity, not by deleting idempotency records.
- `tests/test_backup_idempotency_retention.py` enforces durability across a
  restart, conflict detection after a restart and replay of terminal records.
  Its static guard flags literal SQL that deletes, drops or truncates the
  backup tables, and code that unlinks, removes, renames or replaces
  `input-backup.sqlite`. It cannot see dynamic SQL or indirect file
  operations; code review remains the control for those, and any such change
  needs a new ADR.
- Recovery of `blocked` records and of `unknown` records past the worker's
  3000-second retry window is a separate operator contract (task T03d). This
  ADR does not authorize it.
