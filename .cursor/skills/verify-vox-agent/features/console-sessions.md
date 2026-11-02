# Console Sessions

The web console's Sessions tab lists the user's capture sessions and, for the selected one, shows its title, status, detected languages, the transcript by speaker, and the summary, action items, and decisions derived from it.

## Sub-features

- `sessions-list` shows each session's title (or `Untitled session`), mode, status badge, and start time.
- `sessions-detail` shows `Languages:`, the `Transcript` (speaker label + normalized text), and the `Summary` nodes.
- `sessions-derived` shows `Action items` and `Decisions` sections (`No action items.` / `No decisions.` when empty).
- `sessions-empty` shows `No sessions yet.` on a fresh engine.

## How to get to it (user POV)

- Open `http://127.0.0.1:5273/`, click the `Sessions` tab, click a session in the left list.

## Driving it with verify-vox-agent.sh

Preconditions:

- Launched with `--console`; doctor healthy; the Capture pipeline journey has produced `SES` in this run.

- **Empty state (fresh run only).** Before any capture: `$V ui sessions-empty '[{"click":"Sessions"},{"waitText":"No sessions yet."}]'`. Exit `0`; `network.json` shows `GET /v1/capture/sessions` → 200.
- **Detail.** `$V ui sessions '[{"click":"Sessions"},{"click":"Untitled session"},{"waitText":"Summary: cap_"},{"waitText":"Alex (PM)"}]'`. Exit `0`.
- **Assert.** `ui/sessions.text.txt` contains `READY`, `Languages: en, ja, zh`, the 7 transcript lines (first `我们决定七月启动日本发布。`), and `Summary: <SES>`; `ui/sessions.network.json` has `GET /v1/capture/sessions/<SES>`, `/transcript-segments`, `/summary`, all `200`.
- **Screenshot.** `ui/sessions.png` shows the list on the left and the detail card on the right.

## Gotchas

- If the engine is down, the tab silently renders `MOCK_SESSIONS` (`Japan launch planning`, id `cap_mock_japan_launch`). Seeing "Japan launch" text is not proof; the session id `<SES>` and 200s in `network.json` are.
- The first session in the list is auto-selected on load, so detail requests appear even before the click.
- Mock ASR creates no `ACTION_ITEM` or `DECISION` nodes, so `No action items.` / `No decisions.` is the expected state after the capture journey.
- Section titles are CSS-uppercased in `innerText` (`SESSION DETAIL`, `TRANSCRIPT`).
