# Agent query

From the console Agents tab the user asks a question about their memory; the engine retrieves matching memory nodes, the configured answerer composes an answer, and the console shows the answer, the answerer name, and citations. The tab also shows MCP status, the token balance, and recent context.

## Sub-features

- `agents-ask` answers a typed question with `answerer` badge and citation list (`type` badge + title).
- `agents-suggested` suggested prompt chips (`What were the decisions this week?`, `Summarize the Japan launch`, `Draft a follow-up email`) fill the query.
- `agents-balance` shows `100000 TOKENS (user scope)`.
- `agents-recent` lists recent memory node ids and titles.
- `agents-audit` each query records an `agent.query` audit event (query length + citation count, never the text).
- `agents-empty-query` empty query is rejected (`Ask` disabled; API returns 400).

## How to get to it (user POV)

- Console: `Agents` tab → type in `Ask a question about your memory…` → `Ask`.
- (No CLI subcommand; MCP clients use `vox-engine serve-mcp`.)

## Driving it with verify-vox-agent.sh

Preconditions:

- Launched with `--console`; doctor healthy; the Capture pipeline journey has produced a summary node `mem_…` in this run.

- **Ask.** `$V ui agents '[{"click":"Agents"},{"fill":{"placeholder":"Ask a question about your memory…","value":"When do we launch in Japan?"}},{"click":"Ask"},{"waitNoText":"Asking the agent"},{"waitText":"Based on 1 memory item(s)"}]'`. Exit `0`.
- **Assert UI.** `ui/agents.text.txt` has `Based on 1 memory item(s) for “When do we launch in Japan?”: Summary: <SES> — 我们决定七月启动日本发布。…`, badge `mock`, citation `SUMMARY` + `Summary: <SES>`, `100000 TOKENS (user scope)`, and `RECENT CONTEXT` with the `mem_` id. `network.json` has `POST /v1/agents/query` → 200 and no failed requests.
- **Side effect.** `$V db agent-audit "select action, metadata from audit.admin_audit_event where action='agent.query'"` → `agent.query {"queryLength":27,"citations":1}`.
- **Empty query (API).** `$V api empty-query POST /v1/agents/query '{"query":"  "}'` exits `1`; `api/empty-query.status` is `400` (`query is required`).

## Gotchas

- The answerer is the product's `mock` adapter (`VOXAGENT_ANSWERER=mock`): it concatenates cited memory, it does not reason. The `llm` answerer needs provider credentials and is unverified.
- On engine error the tab shows `Engine unreachable — showing an offline placeholder, not a live answer.` or `Engine error (server responded <status>) — showing an offline placeholder.` with mock content. Either notice means the journey failed.
- The `MCP status` line is static text, not a live probe of `serve-mcp`.
- The placeholder ends with a Unicode ellipsis `…`, not three dots.
