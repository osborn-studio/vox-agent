# Vox Agent Organization Edition — Pilot Runbook

> Version: 0.1
> Date: 2026-05-17
> Status: Pilot Draft

## 1. Purpose

This runbook describes how to run a local single-team Vox Agent Organization Edition pilot without exposing secrets, private notes, or non-work content.

## 2. Local Setup

Run from the repository root:

```bash
python -m pytest tests/org_collab -q
python -m pytest tests/test_engine_*.py tests/org_collab \
  --ignore=tests/test_engine_e2e_claude.py \
  --ignore=tests/test_engine_retry.py -q
```

Seed a repeatable demo dataset:

```bash
python -m engine.org_demo_seed --data-dir .voxagent-demo
```

The seed creates:

- one organization: `Vox Agent`
- one team: `Platform Pilot`
- users: PM, engineering manager, Alice Developer, Bob Developer
- agents: Codex Worker, CI Status Agent
- work events: one private note, one team blocker, one completed item

## 3. Capture Policy

Default capture is enabled only for configured workspaces or repositories.

Pilot operators must:

- register only work repositories or project folders
- keep pause/resume controls visible
- treat unconfigured paths as capture-denied
- never capture unrelated personal apps or personal directories by default
- keep raw local paths and repo URLs out of public responses; use workspace ids and hashes

## 4. Admin Setup

For a pilot team:

1. Create the organization.
2. Create one team.
3. Add team members with org roles and team roles.
4. Add agent identities for Codex, CI, GitHub, or local automation.
5. Register configured workspaces or GitHub repositories.
6. Run the permission leak tests before enabling real capture.

Validation command:

```bash
python -m pytest tests/org_collab/test_permissioned_query.py \
  tests/org_collab/test_permissioned_search.py \
  tests/org_collab/test_status_qa.py \
  tests/test_engine_org_isolation.py -q
```

## 5. Daily Operation

Recommended pilot workflow:

- Developers work in configured repositories.
- Work-scope events default to `team_raw`.
- Explicit private spans use `private`.
- Secrets are redacted before persistence.
- Cross-user raw access creates audit records.
- Status answers must include source ids/citations.
- Low-confidence answers must say that visible context is missing.

## 6. Rollback

### Split-database pilot migration

Current releases store organization identity, work events, and access audit in
one versioned `org.db`. Before starting an older pilot directory, inspect the
legacy `work_events.db` and `access_audit.db` without changing them:

```bash
python -m engine.org_migration --data-dir .voxagent-demo
```

Execute only after checking the reported row counts:

```bash
python -m engine.org_migration --data-dir .voxagent-demo --execute
```

Execution checkpoints and backs up every source plus the existing `org.db`,
imports by stable id, archives the split source files, and prints a manifest
path. Repeating the command is safe. To restore the exact pre-import layout,
stop the engine and run:

```bash
python -m engine.org_migration \
  --rollback .voxagent-demo/org-migration-backups/<migration-id>/manifest.json
```

Keep the manifest and backup directory with the pilot evidence.

### Pause or retire a pilot

To pause the pilot:

1. Pause capture for configured workspaces.
2. Stop API/UI entry points.
3. Export or retain audit logs according to the pilot retention decision.
4. Archive or remove local pilot databases:

```bash
mv .voxagent-demo .voxagent-demo.archived
```

Do not delete audit logs until the team has confirmed the retention policy.

## 7. Pre-Open-Source Safety Check

Before committing or pushing:

```bash
git status --short
rg -n --hidden --glob '!node_modules/**' --glob '!.git/**' \
  --glob '!macos/.build/**' \
  'sk-|ghp_|api[_-]?key|password|PRIVATE KEY|BEGIN OPENSSH|Authorization: Bearer'
```

Do not commit:

- API keys or `.env` files
- local SQLite databases
- raw private notes or chat transcripts
- generated demo data directories
- macOS build artifacts
