# Capture pipeline

A paired capture device (the simulated VOXPIN) starts a meeting when its record button is pressed; the user uploads the recording, processes it, and the session becomes `READY` with transcript segments, detected languages, and an auto-generated summary memory node. This is the "killer workflow" that feeds Sessions, Memory, and Agents.

## Sub-features

- `pairing-code` issues an 8-character code and a `voxagent://pair?code=` QR payload.
- `pair` claims a code for an owner/workspace and creates a `CONNECTED` device.
- `simulate-record` creates a `HARDWARE` / `MEETING` capture session linked to the device.
- `upload` stores the audio file for the session.
- `process` runs ASR, stores segments, writes one `SUMMARY` memory node, marks the session `READY`.
- `process-missing` rejects unknown session ids.
- Every step leaves an audit event.

## How to get to it (user POV)

- Terminal: `vox-engine device pairing-code`, `vox-engine device pair <code>`, `vox-engine device simulate-record <device>`, `vox-engine capture upload <session> -f <wav>`, `vox-engine capture process <session>`.
- (Pairing also from the console Devices tab — see `devices.md`.)

## Driving it with verify-vox-agent.sh

Preconditions:

- Doctor healthy; `$V db empty "select count(*) n from capture_sessions"` prints `0`.

- **Missing session.** `$V cli process-missing -- capture process cap_nosuch`. Exit `1`; stdout `Process failed: {'detail': 'capture session not found'}`.
- **Code.** `CODE=$($V cli pairing-code -- device pairing-code | python3 -c 'import json,sys;print(json.load(sys.stdin)["code"])')`. Exit `0`; JSON has `code` (8 upper-case hex), `qrPayload` `voxagent://pair?code=<code>`, `expiresAt` ~10 min ahead.
- **Pair.** `DEV=$($V cli pair -- device pair "$CODE" --owner user_local --workspace ws_demo | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')`. Device id `dev_<12 hex>`, `status: CONNECTED`. Pairing the same code again exits `1` with `pairing code invalid, expired, or already claimed`.
- **Record.** `SES=$($V cli record -- device simulate-record "$DEV" | python3 -c 'import json,sys;print(json.load(sys.stdin)["sessionId"])')`. Event JSON has `type: RECORD_BUTTON_PRESSED` and `sessionId: cap_<12 hex>`.
- **Upload.** `$V cli upload -- capture upload "$SES" -f "$($V wav tone 1)" --pretty`. Stdout `Uploaded 32044 bytes → <scratch>/data/audio/<SES>.wav`.
- **Process.** `$V cli process -- capture process "$SES" --pretty`. Stdout `Session <SES>  status=READY` and `Detected languages: en, ja, zh`.
- **Second view (API).** `$V api summary GET /v1/capture/sessions/$SES/summary`. `status: READY`; `summaryNodes[0].title == "Summary: <SES>"`, `type: SUMMARY`.
- **Side effects.** `$V db rows "select s.id, json_extract(s.data,'\$.status') status, (select count(*) from transcript_segments t where t.session_id=s.id) segs, (select group_concat(node_type) from memory_nodes m where m.source_session_id=s.id) nodes, (select path from session_audio a where a.session_id=s.id) audio from capture_sessions s"` → one row `<SES> READY 7 SUMMARY <scratch>/data/audio/<SES>.wav`. `$V db audit "select action, target_id from audit.admin_audit_event order by created_at"` lists `device.paired <DEV>`, `capture.session.created <SES>`, `capture.audio.uploaded <SES>`.
- **Keep the audio.** `$V keep audio /tmp/vox-agent-verify/$RUN_ID/data/audio/$SES.wav`.

## Gotchas

- The mock ASR ignores the audio content: any WAV (even a tone) yields the same 7 zh/ja/en segments (`Alex (PM)`, `Yuki (JP Lead)`, `Sam (Eng Lead)`). This proves the pipeline, not recognition.
- Upload is optional for the mock path; `process` without upload still succeeds. Keep the upload step anyway so `session_audio` is proven.
- `process` is idempotent: a session that already has segments skips ASR (and ignores `--asr`). Use a fresh `simulate-record` session to test another adapter.
- `--asr sherpa` needs `pip install '.[asr]'` plus models from `scripts/fetch_sherpa_model.py`; without them the engine returns HTTP 500 (`sherpa-onnx model not found`). Unverified on this box.
- Hardware sessions have `title: null`; the console shows them as `Untitled session`, and the summary node is titled `Summary: <session id>`.
- Devices live only in engine memory (`DeviceSimulatorRepository`); they disappear on engine restart, unlike sessions/memory in the scratch SQLite DB.
