# VOXAGENT Web Console

A Vite + React + TypeScript web console for the VOXAGENT engine. It gives
three views — **Sessions**, **Memory**, and **Agents** — against the running
`engine/server.py` FastAPI app, and imports the shared `@voxlog/contracts`
Zod schemas/types directly instead of duplicating types (unlike the Expo
mobile shell, which predates this convention). This is the single source of
truth: if the contract changes shape, `tsc` fails here immediately.

## Running it

From the monorepo root:

```bash
# 1. Build the contracts package (the web console imports its dist/ output)
npm --prefix packages/contracts run build

# 2. Install workspace dependencies
npm install

# 3. Start the dev server
npm --prefix apps/web-console run dev
```

Then open **http://localhost:5273**.

## Pointing at a running engine

The console talks to the engine's REST API at `http://127.0.0.1:18900` by
default. Start the engine in another terminal:

```bash
.venv/bin/vox-engine start
```

The engine allows this console's dev origin (`http://localhost:5273`) for
browser cross-origin requests out of the box. Override the allowlist with
`VOXAGENT_CORS_ORIGINS` (comma-separated, or `*` to allow any origin) — e.g.
`VOXAGENT_CORS_ORIGINS=http://localhost:5273,https://console.example.com`.

If the engine is unreachable (not started, wrong port, network error, or a
request hangs past its timeout), every API call falls back to small,
contract-typed mock/seed data instead of showing a broken screen — so the
console always renders something meaningful standalone. This is a genuine
fallback, not a demo mode toggle: once the engine responds, real data
replaces the mock data on the next fetch.

## What each view does

- **Sessions** — lists capture sessions (`GET /v1/capture/sessions`);
  selecting one loads its status, detected languages, transcript segments,
  and summary/action-items/decisions.
- **Memory** — searches memory nodes (`GET /v1/memory/search`) and browses
  Recent (`GET /v1/memory/recent`) vs Action Items
  (`GET /v1/memory/action-items`), grouped by node type.
- **Agents** — a query box with suggested prompts, a static MCP status line,
  the current token balance (`GET /v1/tokens/balance`), and a recent-context
  preview. Submitting a query is an explicit stub in this shell — no agent
  execution is wired up yet; use `vox-engine serve-mcp` and an MCP client for
  real agent queries.

## Commands

```bash
npm --prefix apps/web-console run typecheck   # tsc --noEmit
npm --prefix apps/web-console test            # vitest run
npm --prefix apps/web-console run build       # tsc -b && vite build
```
