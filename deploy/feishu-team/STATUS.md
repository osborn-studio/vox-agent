# Read-only team service status

Run `sudo vox-team-status` on the team server. The JSON output has schema version
1 and UTC `checked_at_utc`; exit status is 0 (all probes OK), 1 (attention), or
2 (unknown probes without a known failure). Known failures take priority;
`has_attention` and `has_unknown` preserve both dimensions. An unknown probe is never treated as healthy.

The command checks:

- Public HTTPS health and delivery-enabled flag, with a bounded response/timeout.
- Main systemd service state and PID.
- Latest local snapshot filename timestamp/size and last backup task exit.
- Last COS task exit/time, **without reading COS or credentials**.
- Aggregate outbox states and oldest pending/sending age, in one read-only SQLite
  transaction with a query deadline. No actor IDs, events, message IDs or text.
- Backup/COS timers active with a scheduled next run.
- Root, data and backup filesystem free bytes and percentage.

Thresholds: backup/COS task and snapshot age over 26 hours; queued work over ten
minutes; any rejected/blocked/unknown delivery; filesystem free space below 10 GiB or 20%.
These are diagnostics, not automatic retry, deletion or recipient notifications.
Separate data/backup mounts are checked as well. Read-only SQLite transactions
may update WAL coordination sidecars, but never message records.

COS unit success means the existing uploader last exited successfully. It is
flagged if it predates the latest snapshot, but is not a fresh read-back, proof that the newest snapshot was uploaded, or a restore
drill. Snapshot presence does not validate decryption or SQLite integrity.
Endpoint health does not expose a separate worker heartbeat. The command runs
on the production host, so it cannot alert if that host disappears. External
monitoring and an alert recipient remain separate R03 work.

For attention results, investigate the named subsystem. Do not replay unknown
messages to clear a warning: they may already have been delivered. Preserve
blocked items while reviewing authorization. For a failed/stale backup, use the
backup/COS runbooks and retain prior snapshots. No remediation runs implicitly.

Tests use synthetic SQLite data and mocked network/systemd inputs. They cover
missing/corrupt evidence, stale/future timestamps, space thresholds, inconsistent
queue references, bounded health reads and absence of message content in reports.

The backup producer writes `.partial` then atomically renames validated output;
the status scanner ignores partial files. File presence alone is not integrity proof.

Claude CLI reported model `claude-opus-5-5` for the source audit. Its findings
led to rejected-queue detection, separate volume checks, timer checks, COS
timestamp correlation, explicit SQLite closure and failure/unknown separation.
The audit did not execute tests or verify remote COS objects.

## Deployment acceptance — 2026-10-04

Installed revision `b1e3bd9` under `/opt/vox-agent/maintenance/b1e3bd9`, exposed
through `/usr/local/sbin/vox-team-status`. Script SHA-256:
`e9c603ccf784e08e0366cb46fd2ab7e797db6c28d0c235be4d14ce24d05bc907`.
At 09:36 UTC all 11 probes passed (exit 0): no pending/sending/unknown/blocked/
rejected deliveries; three delivered rows; 39,494,643,712 free bytes (46.8%).
Backup and COS last executions succeeded, local snapshot age about 3.8 hours;
next timers were scheduled. This does not claim a new backup/restore execution.
Config SHA-256 and service PID were unchanged before/after. No service restart,
member changes or Feishu API requests. 100 status/access/runtime/login tests
and Ruff passed after audit corrections. This is a standalone utility deployment,
not a new client release or completion of external alerting.

## Mixed queue timestamp hardening — 2026-10-04 19:48 UTC

`MIN(created)` could hide NULL, text, future or positive-infinite timestamps
beside a valid pending row. Validate every pending/sending timestamp within the
same read transaction before reporting a healthy queue. The writer stores REAL
epoch seconds (`engine/input_backup.py`); no schema or message mutation is needed.
Four regression cases failed before the fix. Five invalid-value cases now pass,
including negative infinity as existing coverage. 53 status/access tests, Ruff
and diff checks pass. osstack task `task_0738d36a`, run
`run_7861b863-6f0a-430a-8e92-6c97d0be77de`, records verification in ignored
`build/queue-timestamp/STATE.md`. Claude CLI model `claude-opus-5-5` found no
concrete fix defects; test precondition and connection cleanup suggestions were
applied, followed by another 53-test pass. This change is not yet deployed;
the earlier production acceptance remains tied to revision `b1e3bd9`.

## Hardened diagnostic deployment — 2026-10-04 20:46 UTC

Revision `beb2c2c` is installed under `/opt/vox-agent/maintenance/beb2c2c`;
`/usr/local/sbin/vox-team-status` now points at its wrapper. SHA-256:
`af64f01795549ca4205e6db3314b2e1b7d0e24892f5320d4c40fb5b58751f11a`.
The previous `b1e3bd9` directory is retained for atomic symlink rollback.
Preflight, postflight and osstack remote verification returned all 11 probes OK.
Service PID and protected runtime configuration hash stayed unchanged; there
was no service restart, account mutation or Feishu API operation.

New scheduled executions were observed: backup exited successfully at
2026-10-05 04:00:04 CST (UTC+8), COS at 04:30:36 CST. Latest snapshot was 9,710
bytes and seven snapshots were present. Queue counts remained zero except three
delivered rows; free disk was about 39.5 GB (46.8%). These are local file/systemd
observations, not a new remote object download, decryption or restore drill.

53 local status/access tests passed. osstack task `task_bef10d99`, run
`run_3f26332a-7a5b-4b44-a142-d838d8de6557`, records local tests, live status and
diff validation in ignored `build/status-deploy/STATE.md`. The prior Opus 5.5
source audit applies to the same deployed source; no new audit was claimed.
