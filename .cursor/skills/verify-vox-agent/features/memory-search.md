# Memory search

Users search their voice memory (summaries, action items, decisions derived from captures) by free text and browse recent nodes, from the CLI or the console Memory tab.

## Sub-features

- `memory-search` ranks nodes for a query (`results[].node`, `score`).
- `memory-recent` lists nodes from the last N days.
- `memory-action-items` lists `ACTION_ITEM` nodes (console `Action items` toggle).
- `memory-no-match` shows `No matches found.` / `No matches.`.

## How to get to it (user POV)

- Terminal: `vox-engine memory search "<query>" [--pretty]`, `vox-engine memory recent [--days N] [--pretty]`.
- Console: `Memory` tab (the default tab), type in `Search memory (e.g. Japan launch)`, press `Search`; toggle `Recent` / `Action items`.

## Driving it with verify-vox-agent.sh

Preconditions:

- Doctor healthy; the Capture pipeline journey has produced `SES` and its summary node `mem_…` in this run (console steps need `--console`).

- **CLI search.** `$V cli search -- memory search "Japan launch" --pretty`. Exit `0`; one line `[mem_<8 hex>] Summary: <SES>  (score=…)` (id truncated to 12 chars).
- **CLI no match.** `$V cli search-none -- memory search "zzqqxx" --pretty`. Exit `0`; stdout `No matches.`.
- **CLI recent.** `$V cli recent -- memory recent --pretty`. Lists the same node with today's `createdAt`.
- **Console search.** `$V ui memory '[{"click":"Memory"},{"fill":{"placeholder":"Search memory (e.g. Japan launch)","value":"Japan launch"}},{"submit":"Search memory (e.g. Japan launch)"},{"waitText":"Search results for \"Japan launch\""}]'`. Exit `0`; `ui/memory.text.txt` has `SEARCH RESULTS FOR "JAPAN LAUNCH"`, group `SUMMARY`, `Summary: <SES>`; `network.json` has `GET /v1/memory/search?q=Japan%20launch&limit=20` → 200.
- **Console action items.** `$V ui memory-actions '[{"click":"Memory"},{"click":"Action items"},{"waitText":"No action items."}]'`. Mock ASR creates no action items, so the empty state is expected; `network.json` has `GET /v1/memory/action-items` → 200.

## Gotchas

- CLI `memory search` truncates ids to 12 chars (`mem_` + 8 hex) in `--pretty`; use JSON output to get the full id.
- Search is lexical over title/body; the mock summary contains Chinese/Japanese/English text, so `Japan launch`, `Tokyo`, and `日本` all match.
- With the engine down, the console search silently falls back to mock nodes; check `network.json`.
