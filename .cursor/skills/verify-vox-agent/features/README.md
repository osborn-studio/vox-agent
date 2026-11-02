# Vox Agent verification map

This directory is the maintained source for verifying user-facing Vox Agent behavior on a Linux box. Read this index before driving the app, then use the matching feature file as the recipe. Harness: `verify-vox-agent.sh` (see `../SKILL.md`).

## Baseline preconditions

- `$V setup` has run on the current checkout (`.venv`, `node_modules`, `packages/contracts/dist`).
- `export RUN_ID=<unique>` and `V=.cursor/skills/verify-vox-agent/scripts/verify-vox-agent.sh`, from the repo root.
- `$V launch --console` reported `ready:` (or `$V launch` for CLI-only files) and `$V doctor` ends with `doctor: healthy`.
- The scratch data dir starts empty: no capture sessions, memory nodes, or devices; token balance `100000 TOKENS`.
- Nothing else listens on 18900; never drive an engine this run did not launch.

## Driving conventions

- CLI steps go through `$V cli <label> -- <vox-engine args>`; parse JSON stdout (omit `--pretty` when you need ids).
- Console steps go through `$V ui <label> '<steps-json>'`; buttons by visible text, inputs by exact placeholder.
- Side effects go through `$V db <label> "<sql>"` (read-only).
- Ids are random (`dev_`, `cap_`, `mem_` + 12 hex; pairing codes 8 hex upper-case). Parse them; never hard-code.
- Journeys build on each other in this order: capture pipeline → sessions/memory/agents in the console. Devices is independent.

## Proof and skip reporting

- Capture the user action and a second read-only view of the resulting state (db rows, GET, console screenshot).
- Console proof requires `ui/<label>.network.json` with only 2xx engine requests and ids created in this run; the console silently shows mock data otherwise.
- State that ASR and answers come from the product's built-in `mock` adapters; real speech recognition, `sherpa`, and `llm` answers are unverified unless a run says otherwise.
- Report an unreachable path with the attempted command, exit code, and unmet precondition. Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior, then exactly four H2 sections in this order: `Sub-features`, `How to get to it (user POV)`, `Driving it with verify-vox-agent.sh` (starting with `Preconditions:`), `Gotchas`. Keep implementation details out; name user paths, handles, state, commands, and observable proof.

## Features

- [Capture pipeline](./capture-pipeline.md) — pair a simulated device, press record, upload audio, process it into a transcript and summary memory.
- [Console Sessions](./console-sessions.md) — list capture sessions and read a session's transcript, summary, action items, and decisions.
- [Memory search](./memory-search.md) — search and browse memory nodes from the CLI and the console Memory tab.
- [Agent query](./agent-query.md) — ask a question grounded in memory and get an answer with citations (console Agents tab).
- [Devices](./devices.md) — generate a pairing code and pair a device from the console Devices tab.

Not yet mapped (candidates for `/maintain-verification-skill`): console **Admin** tab (users, credentials, audit log, model routing, retention policy + sweep preview/confirm — destructive confirm needs care), `vox-engine tasks|task|cancel|tools|skills|agents|stats|watch|export` (task execution spawns Claude/Codex CLIs), `serve-mcp` MCP server (`voxagent_mcp/`), translation/speech streaming routes (`/v1/speech`, `/v1/translation`), conversation sessions, org/enterprise edition, `services/api-ts` gateway (`npm run smoke:phase0` needs `apps.desktop.server`), and all native/desktop/mobile shells.
