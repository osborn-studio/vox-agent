# Vox Agent Organization Edition — Task Breakdown

> Version: 0.2
> Date: 2026-05-17
> Status: Draft

## Phase 0: Product And Policy Freeze

- [x] Confirm v1 deployment model
  - Acceptance: single-team private deployment is chosen.
  - Verify: reflected in `01_PRODUCT_DESIGN.md` and `02_TECHNICAL_DESIGN.md`.

- [x] Confirm default visibility policy
  - Acceptance: work-scope raw context is visible to the team by default; private spans and secrets remain protected.
  - Verify: reflected in `04_SECURITY_AND_PERMISSIONS.md`.

- [x] Choose required integrations
  - Acceptance: GitHub first is selected for v1.
  - Verify: integration list appears in technical design.

- [x] Confirm product name
  - Acceptance: the product continues to be named Vox Agent.
  - Verify: document titles and product references use Vox Agent.

## Phase 1: Organization Core

- [x] Add org identity models
  - Acceptance: org, user, team, membership, role, and agent identity are represented.
  - Verify: `tests/org_collab/test_identity.py`.

- [x] Add org storage layer
  - Acceptance: SQLite pilot schema supports org metadata and can later map to Postgres.
  - Verify: `tests/org_collab/test_identity.py`.

- [x] Add request context
  - Acceptance: every API/query can carry requester, org, team, and project context.
  - Verify: `tests/org_collab/test_identity.py` and permission/query tests.

## Phase 2: Work Event Capture

- [x] Normalize existing task/activity records into work events
  - Acceptance: agent task messages and activity log entries can become `WorkEvent`.
  - Verify: `tests/org_collab/test_work_event_ingest.py`.

- [x] Add source adapters
  - Acceptance: voice, agent, terminal, GitHub, and manual notes share a common event interface.
  - Verify: `tests/org_collab/test_work_event_ingest.py`.

- [x] Add redaction pipeline
  - Acceptance: tokens, private keys, `.env` values, and private spans are redacted before storage.
  - Verify: `tests/org_collab/test_work_event_ingest.py`.

## Phase 3: Permissions And Query

- [x] Implement policy engine
  - Acceptance: raw, summary, private, team, project, org, and admin audit scopes resolve consistently.
  - Verify: `tests/org_collab/test_permissioned_query.py`.

- [x] Implement permissioned retrieval
  - Acceptance: candidate search results are filtered before answer synthesis.
  - Verify: `tests/org_collab/test_permissioned_search.py`.

- [x] Implement access audit
  - Acceptance: cross-user queries, denied lookups, raw access, and reclassification are logged.
  - Verify: `tests/org_collab/test_permissioned_query.py`.

## Phase 4: Summaries And Reports

- [x] Add daily individual summaries
  - Acceptance: each summary cites source work events and excludes private events unless owner-only.
  - Verify: `tests/org_collab/test_org_summaries.py`.

- [x] Add team daily standup summary
  - Acceptance: output includes completed, in-progress, blockers, decisions, and next actions.
  - Verify: `tests/org_collab/test_org_summaries.py`.

- [x] Add project progress report
  - Acceptance: report groups status by project, issue, branch, and agent task.
  - Verify: `tests/org_collab/test_org_summaries.py`.

## Phase 5: Product Surfaces

- [x] Add team/project dashboard endpoints
  - Acceptance: dashboard can show team status, project status, blockers, and agent status.
  - Verify: `tests/org_collab/test_org_api.py`.

- [x] Add query endpoint
  - Acceptance: natural language status query returns answer, citations, confidence, and audit ID.
  - Verify: `tests/org_collab/test_org_api.py`.

- [x] Add macOS or web UI entry points
  - Acceptance: users can view personal timeline, team dashboard, project report, and query results.
  - Verify: `cd app/frontend && npm run build`.

## Phase 6: Pilot Readiness

- [x] Create seed/demo data
  - Acceptance: demo org includes PM, manager, two developers, project, tasks, private notes, blockers.
  - Verify: `python -m pytest tests/org_collab/test_demo_seed.py -q`.
  - Demo command: `python -m engine.org_demo_seed --data-dir .voxagent-demo`.

- [x] Run permission leak test suite
  - Acceptance: adversarial queries cannot reveal private events.
  - Verify: `python -m pytest tests/org_collab -q`.

- [x] Write pilot runbook
  - Acceptance: install, capture policy, admin setup, and rollback are documented.
  - Verify: `docs/org-collab/07_PILOT_RUNBOOK.md` is linked from org-collab README.
