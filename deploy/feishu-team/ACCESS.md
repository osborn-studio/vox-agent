# Explicit backup access administration

`engine.backup_access` is an operator-only CLI. It uses the OS account permitted
to read the protected runtime file, not an administrator ID supplied by a client.
It makes no Feishu requests and never prints credentials or config contents.
It manages the existing per-user target allowlist; it is not a new HTTP admin API
or team-wide default grant feature.

## Inspect and preview

On a deployed server with the helper installed:

```sh
sudo vox-backup-access --config /etc/vox-team/runtime.json list
sudo vox-backup-access --config /etc/vox-team/runtime.json \
  --org ORG --target TARGET --user VOX_USER_ID grant
```

Use the target and organization IDs returned by `list`. The user must have signed
in first and exist as an active member of that organization in Vox's identity
store. VOX_USER_ID is the internal identity ID, not an email or guessed Feishu
open_id. The list reflects on-disk config, not the live process's loaded state.
Preview is the default and creates no lock, snapshot, DB or config changes.

## Apply during a maintenance window

1. Preview and check the exact org, target and member.
2. Stop `vox-team.service` and ensure no other operator is concurrently starting
   or editing it. The CLI checks stopped state twice but is not a systemd lock.
3. Repeat the exact command with `--apply`.
4. Inspect its result. Start the service and verify HTTPS health. A config write
   alone is **not** activation; the tool always reports `restart_required`.
5. Recheck the on-disk allowlist and, for a real delivery acceptance, obtain the
   separately required authorization for the synthetic Feishu message.

`revoke` removes only the named member. Already queued work is checked against
the reloaded allowlist before delivery. It cannot retract previously sent messages.
Revoking the final member fails with `LAST_MEMBER_USE_DISABLE_TARGET`; explicitly
use `disable-target` without `--user` if the intention is to disable the entire
mapping. This removes that target rather than writing an empty list incompatible
with older service versions. Tenant-wide `backup_target_ids` grants are refused:
removing an explicit member would not revoke that inherited access.

## Recovery and audit

Each applied change prepares a private directory next to runtime.json:
`runtime.json.access-changes/<change-id>/`. It contains before/after snapshots
and an intent with operator UID, UTC epoch time and both hashes. Snapshots are
0600; the directory is 0700. Treat them as sensitive configuration and include
this directory in the administrator's protected configuration backup policy.

The durable intent is written before atomic replacement. If interrupted or an
error is returned, do not assume a rollback: compare runtime.json's SHA-256 to
the intent's before/after hashes to determine the outcome. Preserve the service
stopped while resolving uncertainty. To roll back, review and restore the
matching before.json using the original runtime file owner/group/mode (the tool
preserves these when applying). Do not blindly restore an old snapshot over a
newer change. Never publish snapshot contents or copy them into a repository.

The helper does not restart services, grant tenant-wide access, retrieve secrets,
replay queued messages, or delete snapshots. Repeated grant/revoke of an already
matching configuration is a no-op. A noncooperating root administrator can still
race any local file operation; changes require exclusive operator coordination.

## Delivery recovery (blocked and out-of-window unknown)

The worker never retries `blocked` records, and stops retrying `unknown`
records 3000 seconds after their first attempt. Use the recovery tool only
after fixing the cause, on the service host:

```bash
python -m engine.backup_delivery_recovery --db <input-backup.sqlite> --org <org> --actor <user> --event <eventId>
```

That previews. To apply, stop the service, then add `--apply --operator <name>
--reason "<why>"`. The preview marks each record `requires_attestation`.
A `blocked` record that was never posted can be requeued directly. For
`unknown`, and for a `blocked` record whose earlier attempt may have posted,
first confirm in the target group that the message is absent, then add
`--attest-not-delivered`; requeuing a delivered message posts it twice. The tool takes the worker lock, changes only records whose status
still matches the preview, and writes an audit row
(`backup_delivery_recovery_audit`) with the operator, OS uid, reason and
attestation. It never prints or stores message text. Re-running is a no-op.
See `docs/decisions/2026-10-08-backup-delivery-recovery-v1.md`.

## Verification

Synthetic tests cover preview/no-op, grant/revoke/disable, cross-org and suspended
users, inherited grants, permissions/symlinks/FIFO, stopped-state checks, durable
intent, failed replacement and concurrent manual edits. Runtime authorization
is checked with the same queued job before grant and after revoke. No production
member was added by these tests.

## Deployment evidence — 2026-10-04 (Asia/Shanghai)

The standalone helper from commit `8e2025b` is installed on the existing team
server at `/opt/vox-agent/maintenance/8e2025b/backup-access.py`, reached through
`/usr/local/sbin/vox-backup-access`. The helper SHA-256 matches local source:
`7f0596593c6257df1d546a1db0df38bad0c9c0fd4eba352813781a2938b2df39`.

Server read-only listing succeeded: one configured target, no tenant-wide target
grant. Runtime config hash and running service PID were unchanged across the
check. HTTPS health returned `ok`, with delivery still enabled. No membership
change, service restart or real Feishu API call was performed. This is a deployed
operator utility, not a new application release or live member-onboarding test.

76 synthetic access/runtime/login tests and Ruff passed. Root npm audit reports
zero vulnerabilities with the previously verified busboy 3.2.2 lockfile patch.
Claude Opus 5.5 was requested for a bounded static review; it returned no output
within 120 seconds and was terminated. This is recorded as unavailable review,
not a pass; the host performed the code review and fault-injection checks.

When the main application release includes this module, the wrapper may switch
to `python -m engine.backup_access` under that verified release. Until then the
standalone helper remains pinned. Removing only the helper command is sufficient
to roll back this deployment; it does not revert or change any membership config.
