# Vox Agent Organization Edition — Security And Permissions

> Version: 0.3
> Date: 2026-07-18
> Status: Implemented for the private single-team API boundary; pilot evidence pending

## 1. Security Principle

Vox Agent Organization Edition must create transparency without becoming surveillance.

The default product stance is:

> Capture configured work context by default, make work-scope raw context visible to the single team, protect private/sensitive spans, and audit every cross-user access.

## 2. Roles

| Role | Default Access |
|---|---|
| Individual Contributor | Own raw events and team work-scope raw events |
| Product Manager | GitHub-linked project raw context, blockers, decisions, summaries |
| Tech Lead | Team/project raw events for active work; agent execution logs |
| Engineering Manager | Team/project raw events, summaries, risk reports, aggregate metrics |
| Admin | Policy configuration, retention, audit export |
| Agent | Scoped tool and workspace access, never human-private events by default |

Production `/v1/org/*` routes resolve organization, actor, and role from the
verified Principal. Body-supplied identity fields are compatibility input only
and never participate in authorization. Team and project membership are
explicit database relationships; knowing an ID is not membership.

## 3. Visibility Labels

| Label | Who Can See | Notes |
|---|---|---|
| `private` | creator only | explicitly private notes or sensitive spans |
| `team_summary` | team members | summary only |
| `team_raw` | team members | default for work-scope context |
| `project_summary` | project members | summary only |
| `project_raw` | project members | raw details visible |
| `org_summary` | organization | high-level status only |
| `admin_audit` | admins/security | audit/security material |

## 4. Policy Rules

### Always

- Classify visibility at ingest.
- Redact secrets before storage where possible.
- Log every cross-user query.
- Require source citations for generated status answers.
- Show capture status to the user.
- Allow users to mark spans private.
- Restrict default-on capture to configured workspaces and repositories.

### Ask First

- Exporting org data outside the team server.
- Changing retention periods.
- Capturing outside configured workspaces.
- Disabling private/pause controls.

### Never

- Never commit API keys or raw user data to Git.
- Never expose private events through summary leakage.
- Never let an LLM answer a status query from unauthorized source events.
- Never use invisible always-on recording.
- Never treat generated summaries as immutable truth without source references.
- Never capture non-work apps or unrelated personal content by default.

The adversarial regression gate is:

```bash
python -m pytest tests/test_engine_org_isolation.py -q
```

It covers forged identity claims, cross-tenant/team/project reads and writes,
private prompt injection, dashboard aggregation, admin-only audit visibility,
suspended credentials, and verified-actor audit attribution.

## 5. Redaction

Redaction should run before persistence and before answer synthesis:

- API keys and tokens
- passwords
- private keys
- `.env` values
- customer secrets
- personal/private spans
- unrelated clipboard content

Redaction outputs:

- redacted body
- redaction reason
- source hash
- whether raw encrypted retention is allowed

## 6. Audit Requirements

Audit events:

- cross-user query
- denied query
- raw event access
- visibility reclassification
- retention policy change
- export
- admin permission change
- agent tool execution over team workspace

Audit records must include:

- requester
- target scope
- policy decision
- reason
- timestamp
- source IDs accessed

## 7. Trust UX Requirements

- Users can see what is being captured.
- Users can pause capture.
- Users can mark a note/session private.
- Users can see who queried their raw work context when policy allows that visibility.
- Summaries show the confidence level and source count.
- Admin policy changes are visible in an audit feed.
