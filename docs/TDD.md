# Vox Agent — Technical Design Document

> Version: 3.2 | Date: 2026-06-01 | Status: Active
> Fusion: Multica scheduling + OpenHarness engine + VoxLog voice core
> Repo: gilosbornhello/vox-agents (private)

---

## 1. System Topology

```
┌───────────────────────────────────────────────────────┐
│                  Vox Agent macOS App                   │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │           SwiftUI (native, 120+ files)          │  │
│  │  ├── ChatWindowView (collapsible HSplitView UI) │  │
│  │  ├── ChatSidebar (Agent tree + sync button)     │  │
│  │  ├── ChatArea (message bubbles + task progress)  │  │
│  │  ├── ChatInputBar (mic + streaming ASR + LLM)   │  │
│  │  └── Settings (8 tabs incl. Sync)               │  │
│  └──────────────┬──────────────┬───────────────────┘  │
│          Direct HTTP        WebSocket                  │
│         (ASR/LLM API)     (real-time events)           │
└─────────────────┼──────────────┼──────────────────────┘
                  │              │
    ┌─────────────┘              └─────────────┐
    ▼                                          ▼
┌──────────────┐              ┌──────────────────────────┐
│ ASR Provider │              │  Python Agent Engine     │
│ (4 primary)  │              │  (OpenHarness layer)     │
│ + 13 backup  │              │  ├── Scheduler (Multica) │
│              │              │  ├── Engine (agent loop)  │
│ LLM Provider │              │  ├── Tool Registry       │
│ (12 sources) │              │  └── WebSocket Hub       │
└──────────────┘              └──────────────────────────┘
                                        │
                              ┌─────────▼──────────┐
                              │ External Agents    │
                              │ • OpenClaw :7860   │
                              └────────────────────┘
```

### Key Architecture Decisions

1. **Pure SwiftUI native app** — no Tauri/React/Electron, <0.5s launch, ~40MB RAM, 2.3MB DMG
2. **ASR/LLM called directly by Swift** — HTTP clients built-in, no Python middleman
3. **Python role: Agent execution engine** (optional sidecar, fusion of OpenHarness)
4. **Three-DB separation** (WCDB-inspired): messages.db / tasks.db / activity.db
5. **WebSocket real-time** — Python Agent Engine pushes execution events to SwiftUI
6. **Agent configs read-only scan** from `~/.osborn/config/agents/`
7. **Resizable command-center UI** — macOS `HSplitView` gives the left Agent tree, center chat, and right inspector native drag resizing; sidebars collapse completely from center-toolbar controls while the chat content keeps a readable max width and fluidly compresses for narrow windows.

### Release Path

The active Vox Agent macOS release path is the SwiftPM app in `macos/`. Build and deploy with `swift test`, `swift build -c release`, replacement of `/Applications/Vox Agent.app/Contents/MacOS/VoxLog`, and ad-hoc bundle verification via `codesign`. See `docs/RELEASE.md`.

---

## 2. Recording & Streaming ASR

### Two Parallel Recording Paths

```
Path A: Hotkey (CGEvent tap global shortcut)
  HotkeyManager.onStart → appState.startRecording()
    → RecognitionSession.startRecording(mode:)
    → AudioCaptureEngine (AVCaptureSession, 16kHz mono PCM16)
    → ASR streaming → LLM polish → TextInjectionEngine paste

Path B: Chat window mic button
  ChatInputBar.micTapped() → periodicTask (300-500ms poll)
    → QwenFlashStreamASR SSE → incremental textInput
    → pause 1.5s → polishInPlace() → SnippetStorage.applyEffective()
```

### AudioCaptureEngine
- AVCaptureSession + AVCaptureAudioDataOutput
- 16000 Hz, mono, PCM Int16, 200ms chunks (6400 bytes)
- `warmUp()` pre-creates session to avoid first-use delay

### RecognitionSession State Machine (actor)
```
idle → starting → recording → finishing → injecting → postProcessing → idle
```
- `sessionGeneration` prevents zombie tasks
- Two-phase start: audioEngine.start() → client.connect() (AudioChunkBuffer)

---

## 3. ASR Subsystem

### Fallback Chain
```
1. Local Qwen3 (SenseVoiceServerManager.currentQwen3Port)
2. vl_qwen_flash config (US key preferred)
3. bailianUS credentials
4. bailian CN credentials
5. nil (cannot recognize)
```

### Streaming ASR (QwenFlashStreamASR)
OpenAI-compatible `/chat/completions`, audio as base64 WAV via `input_audio`, SSE stream response. Uses `URLSessionDataDelegate` for SSE parsing.

---

## 4. LLM Polish

| Trigger | Condition |
|---------|-----------|
| During recording pause | consecutiveSame >= 2, not yet polished |
| Stop recording | Not polished during recording |
| Send message | Final fallback |

Default prompt: `去掉口头禅，修正标点，保持原意，直接返回：\n{text}`

### LLM Provider Model Discovery

`ModelListFetcher` reads model catalogs from OpenAI-compatible `/models` endpoints and caches them under `~/Library/Application Support/VoxLog/model-catalog.json`.

- Settings refresh is provider-scoped: only the currently selected LLM provider is queried.
- The refresh call uses the current form credentials, so a newly pasted API key can fetch models before saving.
- Bailian CN intentionally returns every model ID exposed by the API key, not just IDs containing `qwen`, because free-trial balances and available SKUs vary across DashScope accounts.
- The settings model field merges live catalog results into the picker while preserving custom input for endpoint IDs or newly released models not yet returned by the catalog.

### Chat Input Race Guard

`ChatInputBar` performs dictionary correction and LLM polish asynchronously. To avoid stale text artifacts:

- sending a message resets the input value, ASR/polish state, and the input view identity;
- `polishInPlace()` only writes a polished result back if the visible draft still exactly matches the text that was polished.

This prevents a slow polish request from restoring text after the user has already sent or edited the draft.

---

## 5. Scheduling Layer (Multica Fusion)

### Event Bus
```python
class EventBus:
    def on(event_type, handler)
    async def emit(event_type, payload)
    def subscribe_ws() -> asyncio.Queue
```

### Event Types (12)
```
task:created, task:dispatched, task:progress, task:message,
task:completed, task:failed, task:cancelled,
agent:status, message:created,
system:error, system:health, voice:transcribed
```

### Task Scheduler
```
VALID_TRANSITIONS:
  draft → {planned, cancelled}
  planned → {assigned, cancelled}
  assigned → {running, cancelled}
  running → {completed, failed, blocked, cancelled}
  blocked → {running, cancelled}
```

---

## 6. Engine Layer (OpenHarness Fusion)

### Agent Execution Engine (Dual Mode)
```python
class AgentEngine:
    async def run(agent_id, prompt) -> AsyncGenerator[EngineEvent]:
        if agent.provider in ("claude", "codex"):
            yield from _run_cli(agent, prompt)    # CLI subprocess
        else:
            yield from _run_direct(agent, prompt)  # API direct
```

### Tool Registry
```
Built-in: read, write, edit, glob, grep, bash, web_search, web_fetch
Extension: MCP protocol (reserved)
```

### Permission System (3 levels)
| Mode | Behavior |
|------|----------|
| Default | WebSocket approval before write ops |
| Auto | Execute within path rules + command blacklist |
| Plan | Read-only |

---

## 7. Cross-Device Sync (V3.1)

### Architecture: Git-Native Incremental Sync

```
GitSyncEngine (actor)
  │
  ├── ensureCloned() — clone from GitHub if not present
  ├── gitPull() — get latest from all devices
  ├── exportHistory() — daily JSONL, FileHandle.seekToEndOfFile append
  ├── importHistory() — per-device cursor, only read new date files
  ├── exportAgents/Snippets/Hotwords — JSON per device
  ├── importAll() — scan other devices' folders
  └── gitCommitAndPush() — stage only own folder
```

### Repo Layout (gilosbornhello/voxlog-sync, private)
```
{device-short-name}/
├── manifest.json           # Device info + last sync timestamp
├── history/
│   ├── 2026-04-05.jsonl    # Daily records (append-only)
│   ├── 2026-04-06.jsonl
│   └── 2026-04-07.jsonl
├── agents.json
├── snippets.json
└── hotwords.json
```

### Why Truly Incremental

| Phase | Mechanism | Complexity |
|-------|-----------|-----------|
| Export | Daily JSONL, append new records only | O(new records today) |
| Import | Local manifest tracks `lastImportedDate` per remote device | O(new date files) |
| Transport | `git pull/push` — only changed files | Git native delta |
| Dedup | `HistoryStore.exists(id:)` check before insert | O(1) per record |

### Sync Triggers

| Trigger | Mechanism |
|---------|-----------|
| App launch | `applicationDidFinishLaunching` + 3s delay |
| Window hide (red X) | `NSWindow.willCloseNotification` |
| App quit (Cmd+Q) | `applicationShouldTerminate` → `terminateLater` (≤8s) |
| Manual | Sidebar sync button → `AppState.performSync()` |

### DeviceIdentity
- Hardware UUID via IOKit (`kIOPlatformUUIDKey`), stable across reinstalls
- Short name derived from hostname for directory naming
- No account system needed — device ID suffices for personal use

### Local State (not in git repo)
```
~/Library/Application Support/VoxLog/sync-manifest.json
{
  "lastExportDate": "2026-04-09",
  "importCursors": {
    "osborns-macbook-pro": "2026-04-08"
  }
}
```

---

## 8. Data Storage (Three-DB Separation)

Inspired by WeChat WCDB architecture:

| DB | Purpose | I/O Pattern | Sync |
|----|---------|-------------|------|
| messages.db (history.db) | Voice records | High-freq append, FTS5 | Git sync + Obsidian |
| tasks.db | Task management | Mid-freq CRUD | Not synced |
| activity.db | Audit log | Low-freq append | Not synced |

### Schema: recognition_history
```sql
id TEXT PRIMARY KEY,
created_at TEXT NOT NULL,
duration_seconds REAL,
raw_text TEXT NOT NULL,
processing_mode TEXT,
processed_text TEXT,
final_text TEXT NOT NULL,
status TEXT NOT NULL,
character_count INTEGER,
asr_provider TEXT,
agent TEXT,
device_id TEXT,          -- V3.1: sync device origin
sync_updated_at TEXT     -- V3.1: sync timestamp
```

---

## 9. Swift ↔ Python Communication

### WebSocket
```
SwiftUI ←── ws://127.0.0.1:{port}/ws ──→ Python Agent Engine
Messages: {"type":"task:message","payload":{...}}
```

### REST API (Swift → Python)
```
POST   /v1/tasks              # Create task → trigger Agent
POST   /v1/message            # Send message to orchestrator
GET    /v1/engine/status      # Engine status
GET    /health                # {"status":"ok","active_tasks":N}
```

---

## 10. File Structure

```
~/Developer/ClaudeCode/01_active/voxAgent/
├── macos/VoxLog/                SwiftUI App (main)
│   ├── VoxLogApp.swift          Entry + AppDelegate + auto-sync hooks
│   ├── ASR/                     ASR Provider clients
│   ├── Agent/                   Agent management + task dispatch
│   ├── LLM/                     LLM Provider clients
│   ├── Session/                 RecognitionSession (actor)
│   ├── Database/                HistoryStore (SQLite + sync support)
│   ├── Services/                DeviceIdentity, GitSyncEngine, ObsidianSync...
│   ├── UI/Chat/                 Three-pane main interface
│   └── UI/Settings/             8-tab settings (incl. Sync tab)
├── engine/                      Python Agent Engine (52 modules)
├── skills/                      13 agent skill definitions
├── tests/                       48 test files
├── docs/                        PRD.md, TDD.md, API.md
└── CLAUDE.md                    Project context for Claude Code
```

### Data Directories
```
~/Library/Application Support/VoxLog/   # App data (SQLite, JSON configs)
~/.osborn/config/agents/                # Agent definitions (read-only source)
~/.voxlog/                              # Runtime config (terms, settings, memory)
~/Shared/voxlog-sync/                   # Git sync repo (local clone)
```
