# Vox Agent Organization Edition — Product Design

> Version: 0.2
> Date: 2026-05-17
> Status: Draft

## 1. Product Vision

Vox Agent Organization Edition is an internal collaboration and work-state query tool for product, engineering, AI coding teams, and managers.

It records work context created during coding, agent execution, voice notes, reviews, tests, and project decisions. It then turns that context into permission-aware status answers, daily summaries, project reports, and searchable team memory.

The product keeps the core VoxLog V3 promise:

> Your mouth, coding session, and agent work should have a save button.

It extends that promise to teams:

> The organization should know what work is happening without forcing people into constant meetings.

## 1.1 Confirmed v1 Decisions

| Decision | v1 Answer |
|---|---|
| Team scope | Single internal team |
| Product name | Vox Agent |
| Primary integration | GitHub first |
| Raw context access | Work-scope raw context visible to team members and managers by default |
| Capture mode | Default-on for configured workspaces, with visible pause/private controls |

## 2. Target Users

| User | Primary Need | Product Promise |
|---|---|---|
| Product Manager | Understand feature status, blockers, decisions | Ask "what changed and what is blocked?" without interrupting developers |
| Developer | Preserve context, hand off work, reduce status reporting | Work normally; Vox Agent captures useful context |
| Coding Agent Operator | Coordinate Claude Code, Codex, OpenClaw and local agents | See agent work, logs, outputs, and handoffs in one place |
| Engineering Manager | Understand team health, delivery risk, review load | Daily and weekly summaries generated from real work signals |
| Team Lead | Debug cross-person dependencies | Search permissioned context across code, tasks, and decisions |

## 3. Core Jobs To Be Done

1. As a developer, I want my work context recorded while I code so I can recover decisions, commands, test failures, and agent outputs later.
2. As a product manager, I want to ask for project progress and blockers so I can avoid scheduling a status meeting.
3. As an engineering manager, I want an accurate daily team summary so I can spot risk without reading every chat.
4. As an agent operator, I want to see what each AI agent is doing so I can coordinate parallel coding work.
5. As a team member, I want privacy and permission controls so work transparency does not become surveillance.

## 4. Functional Scope

### 4.1 Work Context Capture

Capture sources:

- VoxLog V3 voice notes and polished transcripts
- Text messages and chat commands
- Agent task lifecycle and streaming outputs
- Terminal commands and test runs
- Git commits, branches, diffs, and pull requests
- Issue/task references from GitHub issues, PRs, commits, checks, and local task IDs
- Manual decision notes and meeting-free updates

Each captured item receives metadata:

- actor identity
- team/project/workspace
- source type
- timestamp
- visibility label
- retention class
- source link or local reference

### 4.2 Permissioned Status Query

Users can ask natural language questions such as:

- "What did Alice finish today?"
- "What is blocking the iOS release?"
- "Which agents are currently running tasks?"
- "What changed in the checkout refactor?"
- "Summarize yesterday's coding work for the platform team."
- "Show me the raw work context behind this blocker."

Answers must cite source context at the highest permission level the requester can see.

### 4.3 Daily Summary And Reports

Generated outputs:

- Individual daily work summary
- Team daily standup summary
- Project progress report
- Blocker/risk report
- Agent execution report
- Weekly manager digest
- Release readiness note

Each report distinguishes:

- completed work
- in-progress work
- blockers
- decisions made
- tests run and current confidence
- next recommended actions

### 4.4 Multi-User Collaboration

Core collaboration objects:

- Organization
- Team
- Project
- Workspace
- User
- Agent
- Work session
- Work event
- Summary
- Query
- Audit record

Collaboration surfaces:

- Personal work timeline
- Team activity timeline
- Project dashboard
- Agent status board
- Search and Q&A
- Daily digest inbox
- Admin permission console

### 4.5 VoxLog V3 Core Retention

The organization edition must keep:

- hotkey voice capture
- ASR provider routing
- LLM polishing
- local archive
- digest generation
- Obsidian/export path
- agent chat UI
- task execution and progress streaming

## 5. Permission Model

Default visibility levels:

| Level | Meaning | Example |
|---|---|---|
| Private | Only creator can see | Personal note, sensitive span, raw scratchpad |
| Team Raw | Single team can see work-scope source details | Task logs, relevant terminal output |
| Team Summary | Team can see generated summary | "Refactored auth middleware; tests pending" |
| Project Raw | Project members can see source details | GitHub PR, branch, task logs |
| Org Summary | Reserved for later multi-team org view | "Payments project is blocked on review" |
| Admin Audit | Admin/security review only | Access logs, policy violations |

Default product rule:

> In v1, the single team can see work-scope raw context by default. Private spans, secrets, credentials, and non-work content remain protected. Cross-user raw context access leaves an audit trail.

## 6. MVP

### Must Have

- Single organization and team model
- User identity and role assignment
- Project/workspace tagging for captured events
- Default-on work-context capture with visible pause/private controls
- Agent task status board
- Permissioned search and Q&A over work events
- Individual and team daily summaries
- Audit log for cross-user queries
- Admin-configurable retention defaults
- GitHub commit/PR/check ingestion

### Should Have

- GitHub issue ingestion
- Project dashboard with blockers and confidence
- Export to Markdown/Obsidian
- Redaction pipeline for secrets and private spans

### Later

- SSO/SCIM
- Slack/Teams bot
- Linear/Jira issue references
- Hosted multi-tenant SaaS
- Compliance archive
- Fine-grained code ownership graph

## 7. Success Metrics

| Metric | Target |
|---|---|
| Status meeting reduction | 30 percent fewer recurring status meetings in pilot team |
| Daily summary adoption | 80 percent of workdays have generated individual summaries |
| Query usefulness | 70 percent of status queries answered without asking a human follow-up |
| Trust | Less than 2 percent of captured events manually reclassified after default policy tuning |
| Engineering signal | At least one blocker or dependency surfaced per week that was not visible in existing tools |

## 8. Remaining Product Questions

1. Which GitHub objects are mandatory on day one: commits, PRs, checks, issues, discussions?
2. Who can override visibility labels after capture?
3. What retention period is acceptable for work-scope raw context?
4. Should team members get notified when someone opens their raw work context?
5. What exact control pauses default-on capture during sensitive work?
