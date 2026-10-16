# Design System — Vox Agent (macOS)

> Read this before making any visual or UI decision in `macos/VoxLog`.
> Tokens live in [macos/VoxLog/UI/DesignSystem.swift](macos/VoxLog/UI/DesignSystem.swift) (`enum TF`).
> This document is the rationale + the contract; `TF` is the implementation.
> High-fidelity mockups of the three source directions: [docs/design/2026-07-17-ui-design-directions.html](docs/design/2026-07-17-ui-design-directions.html)

## Product Context

- **What this is:** Native macOS voice-first agent command center. SwiftUI app + Python engine (scheduler, permissions, memory, plugins) over localhost REST/WebSocket.
- **Who it's for:** Power users running multiple AI agents on real work — starting with the owner.
- **Positioning:** NOT a chat app, NOT a web console. An agent cockpit where voice is the primary input device.
- **Project type:** Native macOS three-pane workbench (sidebar / conversation / inspector).
- **Naming boundary:** **Vox Agent** is the user-facing product name (including `MenuBarExtra`). `VoxLog` remains the existing Swift app/module and source-directory name. Design work must not opportunistically rename code symbols or persistence paths.

## Design Thesis

One sentence: **Calm native chassis, operations-grade depth, voice as the signature moment.**

Merged from three reviewed directions (see mockup file):

1. **Calm Native Cockpit** → the chassis. HIG-faithful, quiet, zero decoration. Buys trust.
2. **Agent Operations Desk** → the depth. Status grammar, inline permission approval, ops inspector. Answers "why not a web console."
3. **Voice Command Center** → the signature. Three-state voice stage, countdown ring, global push-to-talk HUD. Answers "why is it called Vox."

**Mood:** A professional instrument, silent until it has something to say. The static UI is deliberately plain so that voice activity (amber) and agent activity (status colors) are the only things that ever move or glow.

## Aesthetic Rules (non-negotiable)

- Follow macOS HIG. System materials (`.sidebar`, `.titlebar`), system controls, native focus rings.
- **No decoration color.** Every non-neutral color on screen must carry a status or voice meaning.
- No toasts, no modals for status. Errors and state changes render in place (inline banners, inline cards).
- No custom chrome. Traffic lights, unified toolbar, standard split view.
- Hover-reveal for message actions; nothing competes with content at rest.
- Anti-patterns banned: gradients as decoration, bubbles for chat, badges without a pending human action, animation outside voice surfaces.

## Typography

System fonts only. This is a native tool; SF **is** the brand.

The names below are semantic roles, not additional `TF` constants. Step 1 adds only the three functional color tokens listed below; use native SwiftUI font APIs for these roles until typography is centralized deliberately.

| Role | Font | Size/Weight | Usage |
|---|---|---|---|
| `body` | SF Pro Text | 13 regular | Messages, descriptions |
| `bodyEmphasis` | SF Pro Text | 13 semibold | Agent names, card titles |
| `secondary` | SF Pro Text | 12 regular | Sidebar sublines, inspector rows |
| `caption` | SF Pro Text | 11 regular | Timestamps context, hints |
| `sectionHeader` | SF Pro Text | 11 semibold, uppercase, +0.06em tracking | Sidebar groups, inspector section titles |
| `transcriptLive` | SF Pro Text | 15 regular | Live ASR text in voice stage ONLY (read-speed while speaking); returns to 13 once finalized |
| `mono` | SF Mono | 11 regular | Task IDs, timestamps, cron, log lines, latency numbers |
| `monoMicro` | SF Mono | 10 regular | Edge annotations (durations, ETAs) |

Rules:
- **All numerals in status/ops surfaces use `tabular-nums`** (`.monospacedDigit()`).
- SF Mono marks "machine truth" (IDs, logs, cron, latency). Never use it for prose.
- Chinese renders via system PingFang fallback; do not pin a CJK font.

## Color

Base palette = existing `TF` tokens. Values are sRGB (light / dark).

### Neutrals (existing)

| Token | Light | Dark | Usage |
|---|---|---|---|
| `TF.appBackground` | 0.96 0.97 0.98 | 0.09 0.10 0.11 | Window content background |
| `TF.surface` | 1.00 1.00 1.00 | 0.13 0.14 0.15 | Cards, input bar, inspector panels |
| `TF.surfaceRaised` | 0.92 0.95 0.97 | 0.18 0.19 0.20 | Hover, selected rows |
| `TF.line` | 0.83 0.86 0.89 | 0.25 0.26 0.28 | Separators, card borders |

Text: system `labelColor` hierarchy (primary/secondary/tertiary). Do not invent text grays.

### Functional colors

| Token | Light | Dark | Meaning |
|---|---|---|---|
| `TF.accent` (existing) | 0.18 0.43 0.72 | 0.32 0.58 0.86 | Brand accent, selection, links, **running** status |
| `TF.success` (existing) | 0.35 0.65 0.35 | 0.42 0.70 0.42 | Completed, passing, connected |
| `TF.warning` (**add**) | 0.85 0.52 0.04 | 0.91 0.58 0.10 | **Awaiting approval** — the only color that means "a human must act" |
| `TF.danger` (**add**) | 0.85 0.25 0.28 | 0.90 0.28 0.30 | Failed, disconnected, destructive |
| `TF.idle` (**add**) | 0.68 0.68 0.66 | 0.42 0.42 0.44 | Idle/archived agents, disabled |
| `TF.amber` (existing) | 0.76 0.49 0.16 | 0.83 0.57 0.24 | **Voice-exclusive**: mic, waveform, listening border, HUD, dictionary-fix underline, @token chips |
| `TF.recording` (existing) | 0.84 0.34 0.27 | 0.87 0.38 0.30 | Live capture indicator (mic fill, menu bar dot while audio is flowing) |

### Exclusivity rules

- `TF.amber` / `TF.recording` appear **only** on voice surfaces (input stage, HUD, playback waveforms, transcript annotations, menu bar recording state). Nowhere else, ever. Static UI stays neutral so "amber = the machine is listening" stays unambiguous.
- `TF.warning` appears **only** as status forms (dot, edge bar, badge, strip segment, inline permission card edge). It never fills buttons or backgrounds. Amber(voice) vs warning(approval) are near hues; they never collide because their *forms* never overlap.
- The settings palette (`TF.settings*`, warm cream) is a separate scoped world for Settings windows only. Do not leak it into the main window.

### Dark mode

Both schemes are first-class (tokens are adaptive `NSColor` dynamic providers — keep it that way). Dark mode reduces saturation slightly (already encoded in values above); never brighten status colors for "pop."

## Status Color Grammar (the one grammar, everywhere)

Four states, one vocabulary, four render forms. Learn once, read everywhere:

This table specifies the target behavior. The current `MenuBarExtra` exposes recording state only, and the status strip does not exist yet; both are Step 1 work, not existing capabilities.

| State | Color | Sidebar (form: dot/edge bar) | Status strip | Timeline card edge | Menu bar |
|---|---|---|---|---|---|
| Running | `TF.accent` | 2pt left edge bar (roster) / 8pt dot | `● N running` | blue edge | normal glyph |
| Awaiting approval | `TF.warning` | edge bar + count badge | `● N awaiting` | orange edge + inline Approve/Deny | orange badge dot |
| Failed | `TF.danger` | edge bar | `● N failed` | red edge | red badge dot |
| Idle / done | `TF.idle` / `TF.success` | gray dot | — | green edge (done events) | normal glyph |

Rules:
- Status is **never color-only**: always paired with a glyph or word (accessibility + grayscale).
- A badge number = pending human actions, nothing else. No unread-message badges.
- `MenuBarExtra` mirrors the strip aggregates; clicking a pending approval focuses the corresponding inline permission card. Never approve an unseen scope directly from the menu.

## Spacing & Layout

- **Grid:** 4pt base. Use existing `TF.spacingXS/SM/MD/LG/XL` (4/8/12/16/24).
- **Radii:** existing `TF.cornerSM/MD/LG` (6/10/16). Chips/inline = SM, cards = MD, HUD = LG, mic button = circle.
- **Three panes:** sidebar 260pt (collapsible; ops roster mode may compress to 240) / center flexible, min 480 / inspector 300pt default, 340–380 in ops mode. Inspector toggles with `⌘⌥I`.
- **Row heights:** sidebar single-line 32pt; roster two-line 48pt; inspector/ops rows 28pt (compact — density is the ops aesthetic); timeline event cards self-sizing with 12pt padding.
- **Status strip:** 30pt, sits under the toolbar, mono 11.
- **Input bar / voice stage:** 52pt at rest (existing `TF.barHeight`), expands to ~120pt while listening.
- **Message flow:** turn-based, not bubbles. User turn = 2pt `TF.accent` left rule + waveform glyph + text. Agent turn = plain text block, max width 660pt. Tool calls collapse to one mono disclosure row. 20pt between turns, hairline divider between exchanges.

These are target dimensions, not a description of the current build. Existing three-pane and conversation geometry is centralized in `ChatLayoutMetrics` (`leftSidebarMinWidth` / `leftSidebarExpandedWidth`, `conversationMinWidth`, `inspectorMinWidth` / `inspectorExpandedWidth`, and `conversationContentMaxWidth`). Change those metrics instead of adding literals to views. `TF.barHeight` belongs to the existing floating-bar design; `ChatInputBar` currently uses `ChatLayoutMetrics.inputMinHeight` / `inputMaxHeight`, so its 52pt resting target must be implemented there.

## Motion

Discipline: **animation is information.** Only these are allowed:

| Animation | Spec (existing token where possible) | Where |
|---|---|---|
| Voice stage expand/collapse | `TF.springSnappy` (0.35/0.8) | Input bar ↔ listening |
| Listening border breathe | 2.2s easeInOut loop, opacity 0.55↔0.18 | Voice stage + HUD only |
| Waveform bars | live amplitude-driven (never fake loops in shipping UI) | Voice surfaces only |
| Pause countdown ring | 1.5s linear stroke, interruptible | Around mic button |
| Polish crossfade (raw→final) | 150ms crossfade, no reflow theatrics | Transcript text |

Everything else, including hover-reveal actions and running-status indicators: no animation. `Reduce Motion`: replace breathe/waveform with a static level meter + text state; keep the 150ms crossfades.

## Voice State Machine (input stage)

| State | Mic | Waveform | Transcript | Right status (mono 10) | Exit |
|---|---|---|---|---|---|
| **Idle** | neutral glyph, `TF.surface` | hidden/flat gray | placeholder hint | provider + latency | hotkey/click → Listening; typing → plain text mode |
| **Listening** | filled `TF.recording` | live amber bars | words appear as recognized, tertiary gray, 15pt | `● LISTENING` + provider | pause 1.5s → Counting; Esc → Idle (keep text) |
| **Counting** (substate) | ring draws 1.5s linear | live | unchanged | "pause detected" | speech resumes → Listening; ring completes → Processing |
| **Processing** | neutral, spinner in status | frozen, gray | dims to 45% | "polishing · dict lookup" | polish done → Finalized |
| **Finalized** | neutral | hidden | polished text, 13pt black; `@agent` tokens amber chips; dictionary fixes dotted-underlined | "N words fixed · ↩ send · esc re-record" | ↩ send; edit inline; esc discard |

Trust anchors (required, not optional):
- Every voice message keeps its **audio (replayable)** + **raw transcript (expandable)** + fix markers.
- Inspector Voice panel: provider + latency, failover status, polish diff (raw → final), **one-click "add to dictionary"** for any mis-recognized word.

## Key Components

- **Sidebar agent row:** status form + name (13 semibold) + subline (12 secondary: current task or idle). Voice-target agent gets a 1.5pt `TF.amber` inset ring; digits 1–9 switch targets. Drag a message onto an agent = forward.
- **Status strip:** `N running / N queued / N awaiting / N failed` segments (click = filter timeline) + engine WS state + next cron, right-aligned mono.
- **Timeline:** messages and task events interleaved. Event card = `TF.surface`, `cornerMD`, 2pt status edge, mono step log inside a disclosure.
- **Permission card (inline, the flagship interaction):** warning edge; title "«Agent» requests permission"; scope line with `code` chips (exact paths/commands); expiry; buttons Approve (success fill) / Deny (quiet) / "Always for this file…". Approving updates the card in place to an audit line. Never a sheet, never a modal.
- **Inspector:** default mode = segmented Task / Memory / Permissions / Log. Ops mode = stacked Now Running / Queue & Schedule / History & Audit (no tabs; each section collapsible, 28pt rows, pause/cancel/rerun on hover).
- **Push-to-talk HUD:** 400×~84pt (`TF.barWidth`), `cornerLG`, dark material in both appearances, always-on-top. Contents: recording dot + "listening · release to send" + target agent (mono, amber) + live waveform + live transcript + footer hints (esc cancel · tab switch target). Never silent capture: HUD, menu bar state, stage border, and the system orange mic dot are four redundant indicators.

## Accessibility & Privacy

- Text must meet WCAG 2.2 AA contrast (at least 4.5:1 for normal text) on its actual surface in both appearances. Functional colors are supplementary signals, not approved text colors: several light-mode status tokens do not reach 4.5:1 on `TF.appBackground`, so verify each rendered form and keep the required glyph/word label.
- Status never color-only (glyph/word pairing rule above).
- VoiceOver: announce voice state transitions ("listening", "polishing", "finalized, two words corrected"); permission cards are actionable containers.
- Recording indication is quadruple-redundant; capture never starts without visible state change. This is a red line.

## Adoption Baseline

This document is the accepted target contract; the existing UI predates it and is not fully compliant yet. Known migration work includes hardcoded `Color.accentColor` / `.red` and local radii in `ChatSidebar`, `ChatInputBar`, and adjacent chat views; the missing shared status model; and a recording-only `MenuBarExtra`. Do not add new exceptions. When touching one of these surfaces, migrate the affected code to `TF` and the shared status grammar rather than treating the pre-existing literal as precedent.

## Implementation Map

1. **Extend `TF`** with `warning`, `danger`, `idle` (adaptive, values above) + a nested `TF.Status` enum mapping state → color/glyph/label so all four render forms consume one source without introducing an ambiguous module-level `Status` type.
2. **Step 1 — status grammar:** `ChatSidebar` rows + a new status strip in the three-pane workbench + the existing `MenuBarExtra` in `VoxLogApp.swift`. Update shared `ChatLayoutMetrics` for any geometry changes. Smallest change, biggest capability signal.
3. **Step 2 — permission inline card** in the message timeline (engine permissions API already exists).
4. **Step 3 — voice stage state machine** + countdown ring + polish crossfade in `ChatInputBar`. The view must drive the existing `RecognitionSession`; `HotkeyManager` uses that same session, and `RecognitionSession` remains the owner of `AudioCaptureEngine`. Do not instantiate or own the audio engine in a view.
5. Second tier (decide after usage data): full ops inspector, global HUD.

## Decisions Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-17 | Three directions reviewed; merged system adopted (calm chassis / ops depth / voice signature) | See docs/design/2026-07-17-ui-design-directions.html |
| 2026-07-17 | Keep `TF.accent` blue as brand + running color (drop the teal explored in mockup 01) | Existing codebase already standardized on blue; churn buys nothing |
| 2026-07-17 | Amber reserved exclusively for voice; approval uses `warning` with form-level separation | Two near hues never collide because dots/bars/badges vs mic/waveform/border never overlap |
| 2026-07-17 | Bubbles rejected; turn-based flow with 2pt rule | This is a cockpit, not a chat app |
| 2026-07-17 | No toasts/modals; all status renders in place | Calm principle; permission card is the flagship example |
