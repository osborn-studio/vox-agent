# Vox Agents API Reference

Base URL: `http://127.0.0.1:{port}`
WebSocket: `ws://127.0.0.1:{port}/ws`

## Request correlation and log boundary

HTTP clients may send `X-Request-ID` using 8–64 ASCII letters, digits,
underscores, or hyphens. The engine and TypeScript gateway echo the accepted
value on every HTTP response; missing or invalid values are replaced with a
generated ID. The gateway propagates the same ID to the engine.

Request logs contain only the timestamp, correlation ID, HTTP method, route
template, status, and duration. They do not log process or host identity, query
strings, concrete path parameters, headers, bodies, response bodies, or
exception messages. A Mobile diagnostic report can therefore be joined to
server metadata by correlation ID without collecting a transcript, prompt,
memory body, capability, credential, or session identifier.

## Core

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/message` | Main entry — voice/text → intent → action |
| POST | `/v1/intent` | Classify text intent without executing |
| POST | `/v1/route` | Auto-select best agent for a task |
| POST | `/v1/route/suggest` | Suggest top N agents |
| GET | `/health` | Quick health check |
| GET | `/v1/health/detailed` | Comprehensive diagnostics |
| GET | `/v1/validate` | Pre-flight config validation |
| GET | `/v1/engine/status` | Engine status + active task count |

## Tasks

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/tasks` | Create a task |
| GET | `/v1/tasks` | List tasks (filter: agent_id, status) |
| GET | `/v1/tasks/{id}` | Task details |
| POST | `/v1/tasks/{id}/cancel` | Cancel running task |
| GET | `/v1/tasks/{id}/messages` | Task execution messages |

## Batch

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/batch/create` | Create multiple tasks |
| POST | `/v1/batch/cancel/{agent_id}` | Cancel all agent tasks |
| POST | `/v1/batch/status` | Multi-task status query |

## Search

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/search/tasks` | Full-text + filtered task search |
| GET | `/v1/search/messages` | Search execution messages |
| GET | `/v1/search/recent` | Recent activity feed |

## Agents

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/agents` | List all agents |
| GET | `/v1/agents/{id}` | Agent details |

## Templates

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/templates` | List task templates |
| GET | `/v1/templates/{id}` | Template details |
| POST | `/v1/templates/use` | Create task from template |

## Schedules

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/schedules` | List recurring schedules |
| POST | `/v1/schedules` | Create schedule |
| DELETE | `/v1/schedules/{id}` | Delete schedule |
| POST | `/v1/schedules/{id}/enable` | Enable schedule |
| POST | `/v1/schedules/{id}/disable` | Disable schedule |

## Memory

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/memory` | List agents with memory |
| GET | `/v1/memory/{agent_id}` | Agent memory entries |
| POST | `/v1/memory` | Add memory entry |
| DELETE | `/v1/memory/{agent_id}` | Clear agent memory |

## Profiles

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/profiles` | List behavior profiles |
| GET | `/v1/profiles/{id}` | Profile details + config |

## Observability

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/analytics` | Engine-wide stats |
| GET | `/v1/analytics/{agent_id}` | Per-agent stats |
| GET | `/v1/costs` | API cost summary |
| GET | `/v1/rate-limits` | Rate limit usage |
| GET | `/v1/logs/{task_id}` | Task execution log |
| GET | `/v1/logs/{task_id}/timeline` | Human-readable timeline |
| GET | `/v1/logs/{task_id}/errors` | Task errors |

## Tools & Skills

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/tools` | List registered tools |
| GET | `/v1/skills` | List loaded skills |
| GET | `/v1/plugins` | List loaded plugins |

## Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/webhooks` | List registered webhooks |
| POST | `/v1/webhooks` | Register webhook |
| POST | `/v1/webhooks/{id}/receive` | Receive webhook event |

## Heartbeat

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/heartbeat` | All agent statuses |
| GET | `/v1/heartbeat/{agent_id}` | Agent status |
| POST | `/v1/heartbeat` | Report agent heartbeat |

## Export

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/export/obsidian` | Export to Obsidian vault |
| GET | `/v1/export/daily` | Daily report (Markdown) |
| GET | `/v1/export/weekly` | Weekly report (Markdown) |
| GET | `/v1/export/csv` | Export tasks as CSV |
| GET | `/v1/export/json` | Export tasks as JSON |

## Infrastructure

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/workspaces` | List active workspaces |
| GET | `/v1/workspaces/usage` | Disk usage |
| POST | `/v1/workspaces/cleanup` | Clean old workspaces |
| GET | `/v1/conversations` | Active conversations |
| GET | `/v1/permissions/pending` | Reconcile live approvals initiated by the same verified principal/workspace |
| POST | `/v1/permissions/approve` | Approve or deny one same-principal live request; duplicate, expired, and wrong-principal IDs return 404 |

Permission endpoints require a product principal. Enterprise requests without a
persisted initiating principal fail closed. Personal edition retains a
single-local-principal compatibility path for legacy requests. Pending payloads
contain bounded operational scope only; prompt, transcript/content, credential,
token, password, and secret fields are omitted, and sensitive command/URL
values are redacted. See
[`docs/security/tfi12-principal-scoped-approvals.md`](security/tfi12-principal-scoped-approvals.md).

## WebSocket

Connect to `ws://127.0.0.1:{port}/ws` for real-time events.

Event types:
```
task:created, task:dispatched, task:progress, task:message
task:completed, task:failed, task:cancelled
agent:status
permission:request
notification
message:created, voice:transcribed
system:error, system:health
```

Message format:
```json
{
  "type": "task:message",
  "payload": {
    "task_id": "abc123",
    "seq": 5,
    "author_type": "agent",
    "author_id": "claude-code",
    "type": "text",
    "content": "Tests passed."
  }
}
```
