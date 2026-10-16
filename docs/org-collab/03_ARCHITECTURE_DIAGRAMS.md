# Vox Agent Organization Edition — Architecture Diagrams

> Version: 0.2
> Date: 2026-05-17
> Status: Draft

## 1. System Context

```mermaid
flowchart LR
  Dev["Developer"]
  PM["Product Manager"]
  EM["Engineering Manager"]
  AgentOp["Coding Agent Operator"]

  Mac["Vox Agent macOS App\nVoice, chat, local capture"]
  Server["Vox Agent Team Server\nPolicy, ingest, search, summaries"]
  Agents["AI Coding Agents\nClaude Code, Codex, OpenClaw"]
  GitHub["GitHub\nCommits, PRs, checks"]
  Issues["Later\nLinear / Jira"]
  Reports["Daily Reports\nProject Progress\nBlockers"]

  Dev --> Mac
  AgentOp --> Mac
  PM --> Server
  EM --> Server
  Mac --> Server
  Mac --> Agents
  Agents --> Server
  GitHub --> Server
  Issues -. later .-> Server
  Server --> Reports
```

## 2. Layered Architecture

```mermaid
flowchart TB
  subgraph Capture["Capture Layer"]
    Voice["Voice + ASR\nVoxLog V3"]
    Chat["Text / Chat"]
    Terminal["Terminal / Tests"]
    AgentEvents["Agent Task Events"]
    Git["Git / PR Events"]
  end

  subgraph Ingest["Ingest Layer"]
    Normalize["Normalize"]
    Redact["Redact Secrets"]
    Classify["Classify Visibility"]
    Resolve["Resolve Project / Workspace"]
  end

  subgraph Policy["Policy Layer"]
    RBAC["Role + Team Policy"]
    Visibility["Visibility Transform"]
    Audit["Access Audit"]
  end

  subgraph Storage["Storage Layer"]
    Events["WorkEvent Store"]
    Summaries["Summary Store"]
    Tasks["Task Store"]
    Identity["Identity Store"]
  end

  subgraph Intelligence["Intelligence Layer"]
    Search["Permissioned Search"]
    QA["Status Q&A"]
    Digest["Daily / Weekly Digest"]
    Risk["Blocker + Risk Detection"]
  end

  subgraph Experience["Experience Layer"]
    App["macOS App"]
    Dashboard["Team Dashboard"]
    API["REST / WebSocket API"]
  end

  Capture --> Normalize --> Redact --> Classify --> Resolve --> RBAC
  RBAC --> Events
  RBAC --> Audit
  Events --> Search
  Events --> Digest
  Tasks --> Risk
  Identity --> RBAC
  Search --> QA
  Digest --> Summaries
  QA --> Dashboard
  Summaries --> Dashboard
  Dashboard --> API
  API --> App
```

## 3. Permissioned Query Flow

```mermaid
sequenceDiagram
  participant U as Requester
  participant API as Query API
  participant P as Policy Engine
  participant R as Retriever
  participant L as LLM Synthesizer
  participant A as Audit Log

  U->>API: "What is blocking checkout?"
  API->>P: Build request context
  P-->>API: Allowed scopes + visibility ceiling
  API->>R: Retrieve candidate events/summaries
  R-->>API: Candidate source IDs
  API->>P: Filter candidates
  P-->>API: Visible + redacted sources
  API->>L: Synthesize answer with citations
  L-->>API: Answer + confidence flags
  API->>A: Record cross-user query
  API-->>U: Permission-aware answer
```

## 4. Daily Summary Flow

```mermaid
flowchart LR
  Events["Work Events\nvoice, agent, git, tests"]
  Window["Daily Window\nuser/team/project"]
  Policy["Summary Visibility Policy"]
  Summarizer["Summary Generator"]
  Store["WorkSummary Store"]
  Review["Optional User Review"]
  Publish["Digest Inbox / Dashboard"]

  Events --> Window --> Policy --> Summarizer --> Store
  Store --> Review --> Publish
  Store --> Publish
```

## 5. Org Data Model

```mermaid
erDiagram
  ORGANIZATION ||--o{ TEAM : contains
  ORGANIZATION ||--o{ USER : contains
  TEAM ||--o{ TEAM_MEMBER : has
  USER ||--o{ TEAM_MEMBER : joins
  ORGANIZATION ||--o{ PROJECT : owns
  PROJECT ||--o{ WORKSPACE : has
  USER ||--o{ WORK_SESSION : starts
  PROJECT ||--o{ WORK_SESSION : scopes
  WORK_SESSION ||--o{ WORK_EVENT : emits
  PROJECT ||--o{ WORK_EVENT : indexes
  WORK_EVENT ||--o{ WORK_SUMMARY : sources
  USER ||--o{ ACCESS_AUDIT : requests
```

## 6. Recommended v1 Deployment

```mermaid
flowchart TB
  subgraph Team["Internal Team Network"]
    Mac1["Developer Mac"]
    Mac2["Manager / PM Browser"]
    Engine["Vox Agent Team Server"]
    DB[("Postgres or SQLite Pilot DB")]
  end

  GitHub["GitHub Private Repos"]
  Issue["Later: Linear / Jira"]
  LLM["LLM Providers"]

  Mac1 --> Engine
  Mac2 --> Engine
  Engine --> DB
  Engine --> GitHub
  Engine -. later .-> Issue
  Engine --> LLM
```
