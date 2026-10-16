#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
NPM_BIN="${NPM_BIN:-npm}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [ -z "${PYTHON_BIN}" ]; then
  echo "python3 not found" >&2
  exit 1
fi

SMOKE_API_TOKEN="$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(32))')"

TS_BASE_URL="${VOXLOG_TS_BASE_URL:-http://127.0.0.1:7901}"
PY_BASE_URL="${VOXLOG_PY_BASE_URL:-http://127.0.0.1:7902}"
SMOKE_BOOTSTRAP="${VOXLOG_SMOKE_BOOTSTRAP:-auto}"
BOOTSTRAP_PY_PORT="${VOXLOG_SMOKE_PY_PORT:-17892}"
BOOTSTRAP_TS_PORT="${VOXLOG_SMOKE_TS_PORT:-17891}"

TMP_WAV="$(mktemp -t voxlog-smoke).wav"
TMP_JSON="$(mktemp -t voxlog-smoke).json"
PY_LOG="$(mktemp -t voxlog-smoke-py).log"
TS_LOG="$(mktemp -t voxlog-smoke-ts).log"
SMOKE_HOME="$(mktemp -d -t voxlog-smoke-home)"
OBSIDIAN_VAULT=""

PY_PID=""
TS_PID=""

cleanup() {
  if [ -n "${TS_PID}" ]; then
    kill "${TS_PID}" 2>/dev/null || true
  fi
  if [ -n "${PY_PID}" ]; then
    kill "${PY_PID}" 2>/dev/null || true
  fi
  rm -rf "$TMP_WAV" "$TMP_JSON" "$PY_LOG" "$TS_LOG" "$SMOKE_HOME" "${OBSIDIAN_VAULT:-}"
}

report_error() {
  local status="$?"
  local line="${BASH_LINENO[0]:-unknown}"
  echo "phase0 smoke failed at line ${line} (exit ${status})" >&2
  for log_file in "$PY_LOG" "$TS_LOG"; do
    if [ -s "$log_file" ]; then
      echo "last lines from $(basename "$log_file"):" >&2
      tail -20 "$log_file" | sed -E 's/(Bearer )[A-Za-z0-9._~-]+/\1[REDACTED]/g' >&2
    fi
  done
  return "$status"
}

trap cleanup EXIT INT TERM
trap report_error ERR

wait_for_http() {
  local url="$1"
  local attempts="${2:-40}"
  for _ in $(seq 1 "$attempts"); do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_json_route() {
  local url="$1"
  local attempts="${2:-25}"
  for _ in $(seq 1 "$attempts"); do
    if curl -sf "$url" >"$TMP_JSON" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

route_ok() {
  local url="$1"
  curl -sf "$url" >/dev/null 2>&1
}

free_port() {
  local port="$1"
  local pids=""
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "$pids" | xargs kill 2>/dev/null || true
    sleep 1
  fi
}

start_bootstrap_services() {
  free_port "$BOOTSTRAP_PY_PORT"
  free_port "$BOOTSTRAP_TS_PORT"

  (
    cd "$ROOT_DIR"
    HOME="$SMOKE_HOME" VOXLOG_PORT="$BOOTSTRAP_PY_PORT" VOXLOG_API_TOKEN="$SMOKE_API_TOKEN" "$PYTHON_BIN" -m apps.desktop.server
  ) >"$PY_LOG" 2>&1 &
  PY_PID="$!"

  (
    cd "$ROOT_DIR/services/api-ts"
    VOXLOG_TS_PORT="$BOOTSTRAP_TS_PORT" \
    VOXLOG_PY_BACKEND_URL="http://127.0.0.1:${BOOTSTRAP_PY_PORT}" \
    VOXLOG_PY_BACKEND_API_TOKEN="$SMOKE_API_TOKEN" \
    "$NPM_BIN" run dev
  ) >"$TS_LOG" 2>&1 &
  TS_PID="$!"

  wait_for_http "http://127.0.0.1:${BOOTSTRAP_PY_PORT}/health" 40
  wait_for_http "http://127.0.0.1:${BOOTSTRAP_TS_PORT}/health" 40
  wait_for_http "http://127.0.0.1:${BOOTSTRAP_TS_PORT}/v1/settings/providers" 40

  PY_BASE_URL="http://127.0.0.1:${BOOTSTRAP_PY_PORT}"
  TS_BASE_URL="http://127.0.0.1:${BOOTSTRAP_TS_PORT}"
}

should_bootstrap() {
  if [ "$SMOKE_BOOTSTRAP" = "always" ]; then
    return 0
  fi
  if [ "$SMOKE_BOOTSTRAP" = "never" ]; then
    return 1
  fi

  if ! route_ok "${TS_BASE_URL}/health"; then
    return 0
  fi
  if ! route_ok "${PY_BASE_URL}/health"; then
    return 0
  fi
  if ! route_ok "${TS_BASE_URL}/v1/settings/providers"; then
    return 0
  fi
  return 1
}

if should_bootstrap; then
  start_bootstrap_services
fi

curl -sf "${TS_BASE_URL}/health" >/dev/null
curl -sf "${PY_BASE_URL}/health" >/dev/null

curl -sf "${TS_BASE_URL}/v1/settings/providers" >"$TMP_JSON"
"$PYTHON_BIN" - <<'PY' "$TMP_JSON"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert "providers" in payload, "provider settings missing"
assert "active_profile" in payload, "active profile missing"
PY

curl -sf "${TS_BASE_URL}/v1/settings/providers/test" >"$TMP_JSON"
"$PYTHON_BIN" - <<'PY' "$TMP_JSON"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert "recommended_stt_provider" in payload, "recommended provider missing"
assert "checks" in payload and payload["checks"], "connectivity checks missing"
PY

"$PYTHON_BIN" - <<'PY' "$TMP_WAV"
import math
import struct
import sys
import wave

path = sys.argv[1]
sample_rate = 16000
duration_seconds = 1
frequency = 440.0
amplitude = 0.2

with wave.open(path, "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(sample_rate)
    frames = bytearray()
    for index in range(sample_rate * duration_seconds):
        value = int(32767 * amplitude * math.sin(2 * math.pi * frequency * (index / sample_rate)))
        frames.extend(struct.pack("<h", value))
    wav.writeframes(frames)
PY

curl -sf -X POST "${TS_BASE_URL}/v1/voice" \
  -F "audio=@${TMP_WAV}" \
  -F "source=smoke-phase0" \
  -F "target_app=Cursor" \
  -F "session_id=smoke-phase0" \
  -F "mode=normal" >"$TMP_JSON"

curl -sf -X POST "${TS_BASE_URL}/v1/voice/text" \
  -H "content-type: application/json" \
  --data '{"text":"gateway phase0 text","source":"mobile-bot","target_app":"mobile","session_id":"smoke-phase0","mode":"normal","role":"other"}' >/tmp/voxlog-voice-text.json

"$PYTHON_BIN" - <<'PY' /tmp/voxlog-voice-text.json
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert payload["utterance_id"], "text voice utterance id missing"
assert payload["stt_provider"] == "gateway-text", "text voice provider mismatch"
PY

read -r UTTERANCE_ID OUTPUT_ID < <(
  "$PYTHON_BIN" - <<'PY' "$TMP_JSON"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
print(payload["utterance_id"], payload["output_id"])
PY
)

TEXT_UTTERANCE_ID="$("$PYTHON_BIN" - <<'PY' /tmp/voxlog-voice-text.json
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
print(payload["utterance_id"])
PY
)"

curl -sf "${TS_BASE_URL}/v1/recent" >"$TMP_JSON"
"$PYTHON_BIN" - <<'PY' "$TMP_JSON" "$UTTERANCE_ID" "$TEXT_UTTERANCE_ID"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
recent = payload.get("recent")
assert recent, "recent payload missing"
assert recent["utterance_id"] in {sys.argv[2], sys.argv[3]}, "recent utterance mismatch"
PY

wait_for_json_route "${TS_BASE_URL}/v1/digests/session?session_id=smoke-phase0" 25
"$PYTHON_BIN" - <<'PY' "$TMP_JSON"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert payload["digest_type"] == "session_digest", "session digest type mismatch"
assert payload["session_id"] == "smoke-phase0", "session digest id mismatch"
PY

TODAY="$("$PYTHON_BIN" - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).date().isoformat())
PY
)"

wait_for_json_route "${TS_BASE_URL}/v1/digests/daily?date=${TODAY}" 25
"$PYTHON_BIN" - <<'PY' "$TMP_JSON" "$TODAY"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert payload["digest_type"] == "daily_digest", "daily digest type mismatch"
assert payload["digest_date"] == sys.argv[2], "daily digest date mismatch"
PY

wait_for_json_route "${TS_BASE_URL}/v1/digests/project?project_key=cursor" 25
"$PYTHON_BIN" - <<'PY' "$TMP_JSON"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert payload["digest_type"] == "project_digest", "project digest type mismatch"
assert payload["project_key"] == "cursor", "project digest key mismatch"
PY

curl -sf -X POST "${TS_BASE_URL}/v1/digests/rebuild" \
  -H "content-type: application/json" \
  --data '{"scope":"session","session_id":"smoke-phase0"}' >"$TMP_JSON"
"$PYTHON_BIN" - <<'PY' "$TMP_JSON"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert payload["digest_type"] == "session_digest", "rebuilt session digest mismatch"
assert payload["session_id"] == "smoke-phase0", "rebuilt session id mismatch"
PY

curl -sf -X POST "${TS_BASE_URL}/v1/digests/rebuild" \
  -H "content-type: application/json" \
  --data "{\"scope\":\"daily\",\"date\":\"${TODAY}\"}" >"$TMP_JSON"
"$PYTHON_BIN" - <<'PY' "$TMP_JSON" "$TODAY"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert payload["digest_type"] == "daily_digest", "rebuilt daily digest mismatch"
assert payload["digest_date"] == sys.argv[2], "rebuilt daily date mismatch"
PY

curl -sf -X POST "${TS_BASE_URL}/v1/digests/rebuild" \
  -H "content-type: application/json" \
  --data '{"scope":"project","project_key":"cursor"}' >"$TMP_JSON"
"$PYTHON_BIN" - <<'PY' "$TMP_JSON"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert payload["digest_type"] == "project_digest", "rebuilt project digest mismatch"
assert payload["project_key"] == "cursor", "rebuilt project key mismatch"
PY

curl -sf "${PY_BASE_URL}/v1/digests/export?scope=project&project_key=cursor" \
  -H "Authorization: Bearer ${SMOKE_API_TOKEN}" >"$TMP_JSON"
"$PYTHON_BIN" - <<'PY' "$TMP_JSON"
import sys

text = open(sys.argv[1], "r", encoding="utf-8").read()
assert "# project_digest" in text.lower(), "digest export header missing"
assert "cursor" in text.lower(), "digest export content missing"
PY

OBSIDIAN_VAULT="$(mktemp -d -t voxlog-obsidian-vault)"
curl -sf -X POST "${TS_BASE_URL}/v1/integrations/obsidian/export" \
  -H "content-type: application/json" \
  --data "{\"scope\":\"project\",\"project_key\":\"cursor\",\"vault_dir\":\"${OBSIDIAN_VAULT}\"}" >"$TMP_JSON"
"$PYTHON_BIN" - <<'PY' "$TMP_JSON"
import json
import os
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert payload["ok"] is True, "obsidian export failed"
assert os.path.exists(payload["note_path"]), "obsidian note missing"
PY

AI_MATE_MEMORY_DIR="$(mktemp -d -t voxlog-ai-mate-memory)"
curl -sf -X POST "${TS_BASE_URL}/v1/integrations/ai-mate-memory/export" \
  -H 'content-type: application/json' \
  --data "{\"scope\":\"project\",\"project_key\":\"cursor\",\"base_dir\":\"${AI_MATE_MEMORY_DIR}\"}" \
  > /tmp/voxlog-ai-mate-export.json
python3 - <<'PY'
import json
import os
from pathlib import Path

payload = json.loads(Path("/tmp/voxlog-ai-mate-export.json").read_text())
assert payload["ok"] is True, "ai mate memory export failed"
assert os.path.exists(payload["record_path"]), "ai mate memory record missing"
assert payload["record_path"].endswith(".json"), "ai mate memory record should be json"
PY

curl -sf -X POST "${TS_BASE_URL}/v1/recent/mode" \
  -H "content-type: application/x-www-form-urlencoded" \
  --data "utterance_id=${UTTERANCE_ID}&mode=private" >"$TMP_JSON"

curl -sf -X POST "${TS_BASE_URL}/v1/recent/retry" \
  -H "content-type: application/x-www-form-urlencoded" \
  --data "utterance_id=${UTTERANCE_ID}&provider=" >"$TMP_JSON"

read -r RETRIED_UTTERANCE_ID RETRIED_OUTPUT_ID < <(
  "$PYTHON_BIN" - <<'PY' "$TMP_JSON" "$UTTERANCE_ID"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert payload["retried_from"] == sys.argv[2], "retry source mismatch"
print(payload["utterance_id"], payload["output_id"])
PY
)

curl -sf -X POST "${TS_BASE_URL}/v1/output/undo" \
  -H "content-type: application/x-www-form-urlencoded" \
  --data "output_id=${RETRIED_OUTPUT_ID}" >"$TMP_JSON"

curl -sf -X POST "${TS_BASE_URL}/v1/recent/dismiss" \
  -H "content-type: application/x-www-form-urlencoded" \
  --data "utterance_id=${RETRIED_UTTERANCE_ID}" >"$TMP_JSON"

curl -sf "${TS_BASE_URL}/v1/recent" >"$TMP_JSON"
"$PYTHON_BIN" - <<'PY' "$TMP_JSON"
import json
import sys

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
assert payload["recent"] is None, "recent should be empty after dismiss"
PY
