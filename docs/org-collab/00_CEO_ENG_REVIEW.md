# Vox Agent Organization Edition — Gstack CEO + Eng Review

> Version: 0.2
> Date: 2026-05-17
> Status: Draft for product and engineering review
> Scope: Transform Vox Agent from a personal voice-driven agent console into an organization-internal collaboration and work-state query system.

## 1. Starting Point

The current Vox Agents product is a personal AI command center:

- Voice and text input are captured as durable work memory.
- Agents execute tasks and stream progress back into chat.
- Local-first storage, task logs, audit trail, search, dashboard, and agent routing already exist.
- VoxLog V3 voice capture, ASR, polishing, archive, and digest concepts remain valuable.

The proposed change is not "add team chat." It is a repositioning:

> Vox Agent becomes the organization's work context layer: a permissioned system that records how coding work happens, summarizes progress, and lets authorized people ask "what is happening?" without scheduling another meeting.

## 2. Plan CEO Review

### Recommended Mode: Selective Expansion

The idea is strong enough to expand beyond "VoxLog for teams," but should not become a generic Slack/Jira/Linear replacement. The 10-star product is:

> A work-state intelligence layer for engineering organizations, built from real coding context instead of manually written status updates.

### Product Wedge

Start with coding teams because the signal is uniquely rich:

- Git commits and diffs
- Terminal commands and test runs
- Agent task logs
- Voice notes and decision context
- Pull request and issue metadata
- Daily individual and team summaries

This creates a defensible wedge: status is generated from actual work traces, not calendar theater.

### What To Expand

1. Move from personal memory to organization-scoped work memory.
2. Add identity, teams, projects, roles, and permissioned visibility.
3. Add query surfaces for product managers, developers, coding agents, and managers.
4. Add automatic daily/weekly reports and project health digests.
5. Retain VoxLog V3 as the capture layer for voice and local-first working memory.

### What Not To Expand Yet

1. Do not become a full HR performance surveillance product.
2. Do not replace GitHub, Slack, or docs tools in v1.
3. Do not expose raw private conversations by default.
4. Do not attempt cross-company SaaS multi-tenancy before the permission model is proven.

### Sharp Positioning

Bad positioning:

> A team collaboration chat tool with voice and agents.

Better positioning:

> A permissioned work-context memory layer for engineering organizations.

Best first-market sentence:

> Vox Agent reduces engineering status meetings by turning coding context, agent execution, and voice notes into searchable, permission-aware team memory.

## 3. Plan Eng Review

### Engineering Thesis

The organization edition should be built as a thin organization layer over existing primitives:

- `engine.models`: task, task message, activity entry
- `engine.store`: SQLite task/activity storage
- `engine.audit_trail`: tamper-evident audit events
- `engine.search`: task and message search
- `engine.dashboard`: aggregation surface
- `engine.conversation`, `engine.task_log`, `engine.metrics`, `engine.export`

Do not rewrite the engine. Add organization primitives and policy enforcement around it.

### Required New Boundaries

1. Identity boundary: users, service accounts, agents, devices.
2. Organization boundary: org, workspace, team, project.
3. Visibility boundary: raw event, summarized event, redacted event, private event.
4. Query boundary: every query must pass a policy decision before retrieval.
5. Audit boundary: every cross-user lookup must be logged.

### Architecture Decision

Use a staged local-first-to-team-server architecture:

- Phase 1: local/private pilot, single org, SQLite/Postgres-compatible schema, private GitHub or LAN sync acceptable.
- Phase 2: team server, Postgres primary, local clients sync selected work events.
- Phase 3: enterprise mode, SSO, SCIM, retention policy, audit export.

### Biggest Engineering Risk

Permissioned search over mixed raw and summarized work context.

If this is not designed first, the product becomes either:

- too locked down to be useful, or
- too invasive to be trusted.

### Confirmed v1 Product Decisions

1. Team scope: one internal team.
2. System name: Vox Agent.
3. Source-of-truth integration: GitHub first.
4. Capture mode: work-context capture is on by default for configured workspaces.
5. Visibility: product managers, developers, coding team members, and managers can view work-scope raw context by default.

These decisions make the product more operationally useful, but they raise the trust bar. The product must clearly separate work-scope raw context from private notes, secrets, credentials, and non-work content.

### Non-Negotiable Engineering Constraints

- Raw capture must be classified at ingest time.
- Summaries must retain source references and permission labels.
- Team members and managers may see work-scope raw context by default.
- Developers must be able to mark private or sensitive spans.
- Admins can configure retention, export, and visibility defaults.
- Every access to another person's work context is auditable.
- Secrets and non-work private content must be redacted or excluded before persistence.

## 4. Key Product Questions To Resolve

1. What retention period is acceptable for work-scope raw context?
2. Which GitHub objects are mandatory for v1: commits, PRs, checks, issues, discussions?
3. Should users be notified when another member opens their raw work context?
4. What is the default private/pause gesture for a developer during sensitive work?
5. What is the first pilot team and success window?

## 5. Recommended Answer For v1

- Deployment: private single-team deployment.
- Source of truth: GitHub first; Vox Agent stores context, summaries, and query/audit history.
- Visibility: work-scope raw context visible to the team by default; private/sensitive spans remain protected.
- Capture: default-on capture for configured workspaces, with visible status and pause/private controls.
- Buyer wedge: AI-native coding teams using Claude Code, Codex, OpenClaw, and GitHub.
