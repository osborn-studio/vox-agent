# Vox Agent Organization Edition — TDD Development Plan

> Version: 0.2
> Date: 2026-05-17
> Status: Draft

This plan uses test-driven development for the organization collaboration layer. It does not replace the existing VoxLog/Vox Agents test suite; it adds tests around identity, permissions, ingest, summaries, and query safety.

## 1. Test Commands

Existing commands:

```bash
python -m pytest tests/test_engine_*.py \
  --ignore=tests/test_engine_e2e_claude.py \
  --ignore=tests/test_engine_retry.py -q

cd macos && swift test
```

New target commands:

```bash
python -m pytest tests/org_collab/test_demo_seed.py -q
python -m pytest tests/org_collab -q
python -m pytest tests/org_collab/test_permissioned_query.py -q
python -m pytest tests/org_collab/test_work_event_ingest.py -q
```

Demo seed command:

```bash
python -m engine.org_demo_seed --data-dir .voxagent-demo
```

## 2. TDD Slices

### Slice 1: Organization Identity Model

Acceptance:

- Can create org, users, teams, memberships.
- Users can hold org-level and team-level roles.
- Agents can be bound to org and optional owner.

Tests first:

- `test_create_org_user_team_membership`
- `test_user_role_resolution_prefers_team_role`
- `test_agent_identity_has_org_scope`

Implementation targets:

- `engine/org_models.py`
- `engine/org_store.py`
- migrations in existing storage layer

### Slice 2: Work Event Ingest

Acceptance:

- Voice, agent, terminal, git, and manual events normalize to `WorkEvent`.
- Every event has org, actor, source type, visibility, retention class.
- Work-scope events default to `team_raw`; explicit private spans remain `private`.
- Secret redaction runs before persistence.

Tests first:

- `test_voice_transcript_ingests_as_work_event`
- `test_agent_task_message_ingests_as_work_event`
- `test_secret_redaction_before_store`
- `test_default_workspace_event_visibility_is_team_raw`
- `test_private_span_overrides_team_raw_default`

Implementation targets:

- `engine/work_events.py`
- `engine/redaction.py`
- `engine/source_adapters.py`

### Slice 3: Policy Engine

Acceptance:

- Query requester only sees allowed scopes.
- Raw event access requires explicit permission.
- Summary downgrade is applied only when raw access is denied.
- Denied access is audited.

Tests first:

- `test_creator_can_read_private_event`
- `test_teammate_cannot_read_private_event`
- `test_manager_can_read_team_raw_work_context_by_default`
- `test_raw_access_denial_creates_audit_record`
- `test_allowed_cross_user_raw_access_creates_audit_record`

Implementation targets:

- `engine/policy.py`
- `engine/access_audit.py`

### Slice 4: Permissioned Search

Acceptance:

- Search retrieves candidates then filters through policy.
- No unauthorized event body appears in results.
- Search results include visible citations.

Tests first:

- `test_search_filters_private_events`
- `test_project_member_can_see_project_summary`
- `test_search_returns_citation_ids`
- `test_adversarial_query_cannot_bypass_policy`

Implementation targets:

- extend `engine/search.py`
- add `engine/permissioned_retrieval.py`

### Slice 5: Status Q&A

Acceptance:

- Query answers cite source events/summaries.
- Answer synthesis cannot use unauthorized context.
- If source confidence is low, answer says what is missing.

Tests first:

- `test_status_answer_requires_citations`
- `test_status_answer_uses_only_visible_sources`
- `test_low_confidence_answer_reports_unknowns`

Implementation targets:

- `engine/status_qa.py`
- `engine/summary_citations.py`

### Slice 6: Daily Summaries

Acceptance:

- Generate individual, team, and project summaries.
- Summaries store source IDs and visibility.
- Team summary excludes private raw text.

Tests first:

- `test_individual_daily_summary_sources_events`
- `test_team_summary_excludes_private_body`
- `test_project_report_groups_completed_blocked_next`

Implementation targets:

- `engine/org_summaries.py`
- `engine/reporting.py`

### Slice 7: API And Dashboard

Acceptance:

- REST endpoints expose work events, query, summaries, agent status.
- Every endpoint applies request context and policy.
- Dashboard aggregates team/project status.

Tests first:

- `test_create_work_event_endpoint_requires_actor`
- `test_query_endpoint_audits_cross_user_lookup`
- `test_project_dashboard_uses_summary_counts`

Implementation targets:

- extend `engine/server.py`
- extend `engine/dashboard.py`

### Slice 8: Pilot Demo Seed

Acceptance:

- Creates a repeatable pilot org, team, PM, manager, two developers, and agent identities.
- Creates demo work events with a private note, team blocker, and completed item.
- Re-running the seed in one data directory reuses deterministic records instead of growing duplicates.
- Private note is stored but does not leak into team summaries or teammate searches.

Tests first:

- `test_demo_seed_creates_org_people_agents_and_work_events`
- `test_demo_seed_is_idempotent_within_one_store`
- `test_demo_private_note_is_stored_but_not_leaked_to_team_summary_or_query`

Implementation targets:

- `engine/org_demo_seed.py`
- `tests/org_collab/test_demo_seed.py`

## 3. Test Fixtures

Create reusable fixtures:

- `org_alpha`
- `team_platform`
- users: `alice_dev`, `bob_dev`, `pm_user`, `eng_manager`, `admin_user`
- projects: `checkout`, `ios_release`
- events: private note, team summary, project raw agent log, git commit, blocker

Pilot demo fixture IDs live in `engine/org_demo_seed.py` and intentionally use deterministic IDs so a local demo data directory can be reseeded.

## 4. Definition Of Done

- All new org-collab tests pass.
- Existing engine tests still pass.
- Permission denial paths are tested, not only happy paths.
- At least one adversarial query fixture proves no private event leakage.
- Architecture docs updated when implementation changes.
