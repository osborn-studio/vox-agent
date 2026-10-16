# Vox Agent Organization Edition — Technical Design

> Version: 0.2
> Date: 2026-05-17
> Status: Draft

## 1. Technical Objective

Add an organization collaboration layer to the existing Vox Agents architecture without rewriting the personal voice and agent engine core.

The design goal is:

> Capture work events once, classify them at ingest, store them with organization metadata, and answer permissioned work-state questions through summaries and citations.

## 1.1 Confirmed v1 Technical Defaults

- Single-team private deployment.
- GitHub is the first external integration and project source of truth.
- Default-on capture for configured repositories/workspaces.
- Work-scope raw context is readable by the team by default.
- Private spans, credentials, secrets, and non-work content must be redacted or excluded.
- Cross-user raw context access is audited even when allowed.

## 2. Existing Assets To Reuse

| Existing Module | Reuse |
|---|---|
| `engine.models` | Extend task/activity/message models with org metadata |
| `engine.store` | Add organization DB or migrate to Postgres-compatible schema |
| `engine.audit_trail` | Expand into all cross-user access audit |
| `engine.search` | Extend to permission-aware retrieval |
| `engine.dashboard` | Extend to team/project dashboards |
| `engine.conversation` | Use for work sessions and multi-turn project Q&A |
| `engine.task_log` | Source for agent execution traces |
| `engine.metrics` / `analytics` | Inputs for project and team reports |
| VoxLog V3 voice core | Continue as capture and polishing layer |

## 3. Proposed Architecture

### Layered System

1. Capture layer: voice, text, terminal, agent events, GitHub, manual notes.
2. Ingest layer: normalization, redaction, classification, identity binding.
3. Policy layer: role-based access, visibility labels, query authorization.
4. Storage layer: work events, summaries, tasks, identities, audit.
5. Intelligence layer: summarization, embedding/search, status Q&A, report generation.
6. Experience layer: macOS app, web dashboard, CLI/API, optional Slack/Teams bot.

### Deployment Phases

| Phase | Storage | Deployment | Users |
|---|---|---|---|
| Phase 1 | SQLite + local/private Git sync | private pilot | 1 team |
| Phase 2 | Postgres team server + local clients | internal service | multiple teams |
| Phase 3 | Enterprise deployment | SSO/SCIM, retention, audit exports | whole org |

## 4. Data Model Additions

### Identity

```text
Organization
  id, name, created_at

User
  id, org_id, email, display_name, role, status

Team
  id, org_id, name

TeamMember
  team_id, user_id, role

AgentIdentity
  id, org_id, owner_user_id, provider, display_name, visibility_scope
```

### Work Graph

```text
Project
  id, org_id, key, name, source_system, source_ref

Workspace
  id, org_id, project_id, repo_url, local_path_hash, branch

WorkSession
  id, org_id, user_id, project_id, started_at, ended_at, capture_mode

WorkEvent
  id, org_id, session_id, actor_type, actor_id, project_id
  source_type, source_ref, title, body, metadata_json
  visibility, retention_class, created_at

WorkSummary
  id, org_id, scope_type, scope_id, period_start, period_end
  summary_type, body, source_event_ids_json, visibility, created_at

AccessAudit
  id, org_id, requester_user_id, action, target_type, target_id
  policy_result, reason, created_at
```

## 5. Ingest Pipeline

```text
Raw Source Event
  -> Source Adapter
  -> Normalize to WorkEventDraft
  -> Secret/private redaction
  -> Visibility classifier
  -> Project/workspace resolver
  -> Policy pre-check
  -> Store WorkEvent
  -> Emit event to summaries/search/audit
```

### Source Adapters

- Voice adapter: VoxLog transcript and polished text
- Agent adapter: task state, messages, tool use, result
- Terminal adapter: command, exit status, selected output
- Git adapter: branch, commit, diff summary, PR reference
- GitHub adapter: commits, PRs, checks, issues, reviews
- Manual note adapter: typed updates and decisions

## 6. Permissioned Retrieval

Every query runs through:

1. Request context: requester, role, team, project, purpose.
2. Candidate retrieval: search/embedding over events and summaries.
3. Policy filter: remove events requester cannot see.
4. Redaction transform: remove secrets/private spans and downgrade only when policy requires.
5. Answer synthesis: cite visible source IDs.
6. Audit write: record query, target scope, and result class.

No code path should return cross-user data without a policy decision.

## 7. Summary Generation

Summary jobs:

- `individual_daily_summary`
- `team_daily_summary`
- `project_progress_report`
- `blocker_report`
- `agent_execution_report`
- `weekly_manager_digest`

Each summary stores:

- scope
- period
- source event IDs
- model/provider
- prompt version
- visibility label
- confidence flags

## 8. APIs

Initial REST API shape:

```http
POST /v1/orgs/{org_id}/work-events
POST /v1/orgs/{org_id}/github/webhook
GET  /v1/orgs/{org_id}/projects/{project_id}/timeline
POST /v1/orgs/{org_id}/query
GET  /v1/orgs/{org_id}/summaries/daily
GET  /v1/orgs/{org_id}/agents/status
GET  /v1/orgs/{org_id}/audit/access
POST /v1/orgs/{org_id}/visibility/reclassify
```

WebSocket events:

```text
work_event.created
summary.generated
agent.status_changed
query.completed
policy.denied
audit.recorded
```

## 9. Migration Strategy

1. Keep current personal tables intact.
2. Add organization tables behind a feature flag.
3. Backfill existing task/activity records into `WorkEvent` with `org_id = local_default`.
4. Add policy-aware endpoints without removing existing endpoints.
5. Move dashboard/search to new policy layer.
6. Convert sync from personal Git folder to org/team scoped sync only after policy model is proven.

## 10. Engineering Risks

| Risk | Mitigation |
|---|---|
| Permission leaks through search | Policy filter after retrieval and before answer synthesis; test with adversarial fixtures |
| Over-capture damages trust | Default-on capture only inside configured workspaces; visible indicator; pause/private controls |
| Summaries hallucinate status | Source citations required; confidence flags; no uncited claims in status reports |
| Storage grows fast | Retention classes; summarize-then-archive; raw event TTL |
| Tool sprawl | v1 integrates GitHub first; issue trackers move to later phases |
| Local-first conflicts | Server authority for org identity/policy; clients only cache visible data |
