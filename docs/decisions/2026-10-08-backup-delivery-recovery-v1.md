# Operator recovery for blocked and out-of-window unknown backup deliveries

## Status

Accepted (Windows 4.2.8 plan, task T03d, 2026-10-08)

## Date

2026-10-08

## Context

The backup worker only claims `pending` and `unknown` records, and only while
fewer than `RETRY_WINDOW_SECONDS` (3000) have passed since the first attempt.
Two states therefore never progress on their own:

- `blocked`: the worker's pre-send authorization failed (membership or target
  changed). Nothing was posted in that attempt, but an earlier attempt may
  have posted.
- `unknown` after the window: a post may or may not have reached Feishu.

Before this decision the only recovery was ad hoc database edits. Clients
could only show "contact an administrator".

## Decision

1. Recovery is a local operator tool, `python -m engine.backup_delivery_recovery`,
   run on the service host with database access. There is no HTTP endpoint.
2. It previews by default. Applying needs `--apply`, an `--operator`
   identifier and a printable `--reason`, and is bounded to at most 50
   explicit eventIds of one org and actor.
3. Applying resolves the database path and takes the worker's own process
   lock on that canonical path (`<db>.worker.lock`), so it refuses while the
   worker runs even when called through a symlink. Preview opens the existing
   database read-only and never creates or migrates files.
4. `blocked` records can be requeued after the operator fixes the access
   cause. The worker records an authorization denial through
   `BackupStore.block()`, which keeps `first_attempt` NULL only if no POST was
   ever attempted. Such a record was never sent and needs no attestation. A
   `blocked` record with a `first_attempt` (an earlier attempt may have posted,
   e.g. `unknown` then denied on retry, or a record from before this
   bookkeeping) is handled like `unknown` and needs `--attest-not-delivered`.
5. `unknown` records can be requeued only after the window and only with
   `--attest-not-delivered`: the operator checked the group and the message
   is absent. Requeuing a delivered message would post it twice.
6. Every change is compare-and-set against the previewed status and first
   attempt. A record that changed in between is skipped and reported.
7. The status change and an audit row are written in one transaction. The
   audit row records change id, record and event ids, previous status and
   first attempt, operator, OS uid, reason, attestation and time. It never
   contains message text. Re-running is a no-op.
8. A requeued record returns to `pending` with its retry window reset. Its
   eventId, payload and idempotency record are unchanged
   (ADR 2026-10-08-backup-idempotency-retention-v1).

## Consequences

- `blocked` is not a final state for clients. The Windows and macOS clients
  must keep `blocked` records readable and let the user re-check them; the
  wire fixture records `blocked -> pending -> delivered` after an operator
  requeue.
- Attestation is a human judgement. The tool cannot verify it; the audit row
  makes the decision traceable to an operator.
- `tests/test_backup_delivery_recovery.py` covers preview, both recoverable
  states, the attestation gate, ineligible states, idempotency, the worker
  lock, compare-and-set, operator and reason validation, selection bounds,
  per-actor isolation and the CLI's preview default.
