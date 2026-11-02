# Devices

The console Devices tab lists paired capture devices (kind, status, battery, firmware, last seen) and lets the user generate a pairing code and claim it for an owner and workspace. Errors are shown inline (`role=alert`), never as a fake success.

## Sub-features

- `devices-list` shows `No devices paired yet.` or one row per device (`Simulated VOXPIN`, kind badge `SIMULATOR`, `CONNECTED`, `Battery: 100%`, `Firmware: 0.1.0`).
- `devices-generate-code` shows `Code: <8 hex>`, the `voxagent://pair?code=` payload, and `Expires:`.
- `devices-pair` claims a code (fields `Pairing code`, `Owner user id` default `user_local`, `Workspace id` default `ws_demo`) and refreshes the list.
- `devices-pair-invalid` shows `Pairing failed (server responded 404). Check the code hasn't expired and try again.`

## How to get to it (user POV)

- Console: `Devices` tab → `Generate pairing code`; under `Claim code` fill `Pairing code` → `Pair device`.

## Driving it with verify-vox-agent.sh

Preconditions:

- Launched with `--console`; doctor healthy. Devices are in engine memory only; a fresh launch has none.

- **Empty list.** `$V ui devices-empty '[{"click":"Devices"},{"waitText":"No devices paired yet."}]'` (fresh launch only).
- **Generate code.** `$V ui devices-code '[{"click":"Devices"},{"click":"Generate pairing code"},{"waitText":"Code: "}]'`. `ui/devices-code.text.txt` has `Code: <8 hex>` and `voxagent://pair?code=<same>`; `network.json` has `POST /v1/devices/pairing-codes` → 200.
- **Pair.** Get a fresh code the way a second user would: `CODE=$($V cli pc -- device pairing-code | python3 -c 'import json,sys;print(json.load(sys.stdin)["code"])')`, then `$V ui devices-pair "[{\"click\":\"Devices\"},{\"fill\":{\"placeholder\":\"Pairing code\",\"value\":\"$CODE\"}},{\"click\":\"Pair device\"},{\"waitNoText\":\"Pairing…\"},{\"waitText\":\"Simulated VOXPIN\"}]"`. `network.json` has `POST /v1/devices/pair` → 200 then `GET /v1/devices` → 200; `$V api devices GET /v1/devices` lists a device with `kind: SIMULATOR`, `ownerUserId: user_local`, `workspaceId: ws_demo`, `status: CONNECTED`; `$V db dev-audit "select action,target_id,workspace_id from audit.admin_audit_event where action='device.paired'"` has a `ws_demo` row.
- **Invalid code.** `$V ui devices-bad '[{"click":"Devices"},{"fill":{"placeholder":"Pairing code","value":"NOPE0000"}},{"click":"Pair device"},{"waitText":"Pairing failed (server responded 404)"}]'`. Exit `0`; `network.json` shows `POST /v1/devices/pair` → 404 (expected; the driver reports it as a non-2xx request).

## Gotchas

- A code generated in the UI and claimed in the same UI session clears the code card on success; driving both in one `ui` call works only if you read the code from the page, which `console-drive.mjs` cannot do — use the CLI for the code, the UI for the claim.
- Devices are not persisted: they vanish on engine restart.
- With the engine down, the list shows mock `device_mock`, but generate/pair show `Engine unreachable — …` errors instead of faking success.
- `Owner user id` / `Workspace id` are free-text in personal edition; pairing records them as given.
