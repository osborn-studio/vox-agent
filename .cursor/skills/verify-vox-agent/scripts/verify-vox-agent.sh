#!/usr/bin/env bash
# verify-vox-agent.sh — launch, check, drive and tear down an isolated Vox Agent
# engine (+ optional web console) for verification. Verification scaffolding only.
#
# Usage: RUN_ID=<id> verify-vox-agent.sh <command> [args]
#   setup                      create .venv (pip install -e '.[dev,mcp]'), npm ci, build contracts
#   launch [--console]         start engine on 127.0.0.1:18900 with a scratch data dir (+ Vite console on :5273)
#   doctor                     read-only health check of this run's instance
#   env                        print exports for driving this instance by hand
#   cli <label> -- <args...>   run `vox-engine <args>` and record cmd/stdout/stderr/exit
#   api <label> <METHOD> <path> [json-body]   curl the engine and record the response
#   wav <name> [seconds]       write a synthetic 16 kHz mono tone WAV into scratch, print its path
#   db <label> <sql>           read-only SQL against the scratch voice_memory.db / admin_audit.db (see SKILL.md)
#   ui <label> <steps-json>    drive the web console with console-drive.mjs (needs launch --console)
#   keep <label> <path>        copy a scratch artifact into evidence before cleanup
#   cleanup                    stop only the pids this run started, delete scratch, keep evidence
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
SKILL_DIR="$ROOT/.cursor/skills/verify-vox-agent"
RUN_ID="${RUN_ID:-default}"
SCRATCH_BASE="${VOX_VERIFY_SCRATCH:-/tmp/vox-agent-verify}"
SCRATCH="$SCRATCH_BASE/$RUN_ID"
EVIDENCE="${VOX_VERIFY_EVIDENCE:-$HOME/vox-agent-verify-evidence}/$RUN_ID"
ENGINE_PORT=18900   # hard-coded by engine/cli.py (client commands) and apps/web-console/src/api/client.ts
CONSOLE_PORT="${VOX_VERIFY_CONSOLE_PORT:-5273}"   # must be in the engine CORS allowlist (5273 or 5173 by default)
PY="$ROOT/.venv/bin/python"
VOXE="$ROOT/.venv/bin/vox-engine"

die() { echo "verify-vox-agent: $*" >&2; exit 1; }
need_scratch() { [ -d "$SCRATCH" ] || die "no instance for RUN_ID=$RUN_ID (run launch first)"; }
run_env() {
  # Isolated identity: HOME and every data path point into scratch. Personal edition
  # (no bearer token) is the product default; never set VOXAGENT_BOOTSTRAP_ADMIN_TOKEN here.
  export HOME="$SCRATCH/home" VOXAGENT_DATA_DIR="$SCRATCH/data" \
    VOXAGENT_VOICE_MEMORY_DB="$SCRATCH/data/voice_memory.db" VOXAGENT_AUDIO_DIR="$SCRATCH/data/audio" \
    VOXAGENT_EDITION=personal VOXAGENT_ASR_ADAPTER=mock VOXAGENT_ANSWERER=mock
  unset VOXAGENT_LOCAL_TOKEN_SOURCE VOXAGENT_BOOTSTRAP_ADMIN_TOKEN || true
}
port_pid() { ss -ltnpH "sport = :$1" 2>/dev/null | sed -nE 's/.*pid=([0-9]+).*/\1/p' | head -1; }
alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
wait_http() { for _ in $(seq 1 "${2:-40}"); do curl -fsS -o /dev/null "$1" 2>/dev/null && return 0; sleep 0.5; done; return 1; }

cmd_setup() {
  cd "$ROOT"
  [ -x "$PY" ] || python3 -m venv .venv
  "$PY" -m pip install -q -e '.[dev,mcp]'
  npm ci --no-audit --no-fund
  npm --prefix packages/contracts run build
  echo "setup: ok (python $("$PY" -V 2>&1 | cut -d' ' -f2), node $(node -v))"
}

cmd_launch() {
  local console=0; [ "${1:-}" = "--console" ] && console=1
  [ -x "$VOXE" ] || die ".venv missing — run: $0 setup"
  [ -e "$SCRATCH" ] && die "scratch $SCRATCH exists — cleanup RUN_ID=$RUN_ID first or pick a new RUN_ID"
  local owner; owner="$(port_pid $ENGINE_PORT)"
  if [ -n "$owner" ] || ss -ltnH "sport = :$ENGINE_PORT" | grep -q .; then
    die "port $ENGINE_PORT already in use (pid ${owner:-unknown}) — another engine is running; do not drive it. Only one verify instance per host (port is hard-coded)."
  fi
  if [ $console = 1 ] && ss -ltnH "sport = :$CONSOLE_PORT" | grep -q .; then
    die "console port $CONSOLE_PORT already in use"
  fi
  mkdir -p "$SCRATCH/home" "$SCRATCH/data" "$EVIDENCE"
  ( run_env; cd "$ROOT"; exec setsid "$VOXE" start --host 127.0.0.1 --port $ENGINE_PORT ) >"$SCRATCH/engine.log" 2>&1 < /dev/null &
  echo $! >"$SCRATCH/engine.pid"
  for _ in $(seq 1 60); do grep -q "Application startup complete" "$SCRATCH/engine.log" && break; alive "$(cat "$SCRATCH/engine.pid")" || break; sleep 0.5; done
  grep -q "engine ready:" "$SCRATCH/engine.log" || { tail -20 "$SCRATCH/engine.log" >&2; die "engine did not reach 'engine ready:'"; }
  wait_http "http://127.0.0.1:$ENGINE_PORT/health" || die "/health never answered"
  # usable = an authenticated product route answers, not just /health
  curl -fsS "http://127.0.0.1:$ENGINE_PORT/v1/capture/sessions" >/dev/null || die "/v1/capture/sessions not usable"
  if [ $console = 1 ]; then
    [ -f "$ROOT/packages/contracts/dist/index.js" ] || die "contracts not built — run: $0 setup"
    ( cd "$ROOT/apps/web-console"; exec setsid node "$ROOT/node_modules/vite/bin/vite.js" --port "$CONSOLE_PORT" --strictPort --host 127.0.0.1 ) >"$SCRATCH/console.log" 2>&1 < /dev/null &
    echo $! >"$SCRATCH/console.pid"
    wait_http "http://127.0.0.1:$CONSOLE_PORT/" 60 || { tail -20 "$SCRATCH/console.log" >&2; die "console never served"; }
  fi
  cp "$SCRATCH/engine.log" "$EVIDENCE/engine-launch.log"
  echo "ready: engine pid=$(cat "$SCRATCH/engine.pid") url=http://127.0.0.1:$ENGINE_PORT data=$SCRATCH/data$([ $console = 1 ] && echo " console=http://127.0.0.1:$CONSOLE_PORT") evidence=$EVIDENCE"
}

cmd_doctor() {
  need_scratch
  local ok=1 pid; pid="$(cat "$SCRATCH/engine.pid" 2>/dev/null || true)"
  check() { if eval "$2"; then echo "  ok   $1"; else echo "  FAIL $1"; ok=0; fi; }
  echo "doctor RUN_ID=$RUN_ID"
  check "engine pid $pid alive" 'alive "$pid"'
  check "pid cmdline is this checkout's vox-engine start" 'tr "\0" " " </proc/$pid/cmdline 2>/dev/null | grep -q "$ROOT/.venv/bin/vox-engine start"'
  check "port $ENGINE_PORT owned by our pid" '[ "$(port_pid $ENGINE_PORT)" = "$pid" ]'
  check "pid HOME and data dir are scratch" 'tr "\0" "\n" </proc/$pid/environ | grep -qx "VOXAGENT_DATA_DIR=$SCRATCH/data"'
  check "/health status ok" 'curl -fsS http://127.0.0.1:$ENGINE_PORT/health | grep -q "\"status\":\"ok\""'
  check "served OpenAPI version == this checkout's pyproject version" '[ "$(curl -fsS http://127.0.0.1:$ENGINE_PORT/openapi.json | "$PY" -c "import json,sys;print(json.load(sys.stdin)[\"info\"][\"version\"])")" = "$("$PY" -c "import tomllib;print(tomllib.load(open(\"$ROOT/pyproject.toml\",\"rb\"))[\"project\"][\"version\"])")" ]'
  check "personal principal accepted on /v1/capture/sessions" 'curl -fsS -o /dev/null http://127.0.0.1:$ENGINE_PORT/v1/capture/sessions'
  check "voice memory persisted to scratch sqlite" '[ -f "$SCRATCH/data/voice_memory.db" ]'
  if [ -f "$SCRATCH/console.pid" ]; then
    local cpid; cpid="$(cat "$SCRATCH/console.pid")"
    check "console pid $cpid alive" 'alive "$cpid"'
    check "console serves <title>VOXAGENT Console</title>" 'curl -fsS http://127.0.0.1:$CONSOLE_PORT/ | grep -q "<title>VOXAGENT Console</title>"'
  fi
  if [ $ok = 1 ]; then echo "doctor: healthy (RUN_ID=$RUN_ID)"; else echo "doctor: NOT healthy — do not drive"; return 1; fi
}

cmd_env() {
  need_scratch
  cat <<ENV
export HOME="$SCRATCH/home" VOXAGENT_DATA_DIR="$SCRATCH/data" VOXAGENT_VOICE_MEMORY_DB="$SCRATCH/data/voice_memory.db" VOXAGENT_AUDIO_DIR="$SCRATCH/data/audio"
vox-engine() { "$VOXE" "\$@"; }
ENV
}

cmd_cli() {
  need_scratch
  local label="$1"; shift; [ "${1:-}" = "--" ] && shift
  mkdir -p "$EVIDENCE/cli"
  local base="$EVIDENCE/cli/$label" rc=0
  printf 'vox-engine' >"$base.cmd"; printf ' %q' "$@" >>"$base.cmd"; echo >>"$base.cmd"
  ( run_env; cd "$ROOT"; "$VOXE" "$@" ) >"$base.stdout" 2>"$base.stderr" || rc=$?
  echo "$rc" >"$base.exit"
  cat "$base.stdout"; [ -s "$base.stderr" ] && cat "$base.stderr" >&2
  return "$rc"
}

cmd_api() {
  need_scratch
  local label="$1" method="$2" path="$3" body="${4:-}"
  mkdir -p "$EVIDENCE/api"
  local base="$EVIDENCE/api/$label" code
  echo "$method $path ${body}" >"$base.req"
  if [ -n "$body" ]; then
    code=$(curl -sS -o "$base.json" -w '%{http_code}' -X "$method" -H 'content-type: application/json' --data "$body" "http://127.0.0.1:$ENGINE_PORT$path")
  else
    code=$(curl -sS -o "$base.json" -w '%{http_code}' -X "$method" "http://127.0.0.1:$ENGINE_PORT$path")
  fi
  echo "$code" >"$base.status"
  cat "$base.json"; echo
  [[ "$code" == 2* ]] || { echo "HTTP $code" >&2; return 1; }
}

cmd_wav() {
  need_scratch
  local out="$SCRATCH/audio-fixtures/$1.wav" secs="${2:-1}"
  mkdir -p "$(dirname "$out")"
  "$PY" - "$out" "$secs" <<'PY'
import math, struct, sys, wave
path, secs = sys.argv[1], float(sys.argv[2])
with wave.open(path, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(int(16000 * secs))))
PY
  echo "$out"
}

cmd_db() {
  need_scratch
  local label="$1" sql="$2"
  mkdir -p "$EVIDENCE/db"
  "$PY" - "$SCRATCH/data" "$sql" <<'PY' | tee "$EVIDENCE/db/$label.txt"
import sqlite3, sys
data, sql = sys.argv[1], sys.argv[2]
con = sqlite3.connect(f"file:{data}/voice_memory.db?mode=ro", uri=True)
con.execute(f"ATTACH DATABASE 'file:{data}/admin_audit.db?mode=ro' AS audit")
cur = con.execute(sql)
print("\t".join(d[0] for d in cur.description))
for row in cur:
    print("\t".join("" if v is None else str(v) for v in row))
PY
  echo "$sql" >"$EVIDENCE/db/$label.sql"
}

cmd_ui() {
  need_scratch
  [ -f "$SCRATCH/console.pid" ] || die "console not launched (use: launch --console)"
  mkdir -p "$EVIDENCE/ui"
  node "$SKILL_DIR/scripts/console-drive.mjs" "http://127.0.0.1:$CONSOLE_PORT/" "$EVIDENCE/ui/$1" "$2"
}

cmd_keep() {
  need_scratch
  mkdir -p "$EVIDENCE/kept/$1"
  cp -a "$2" "$EVIDENCE/kept/$1/"
  echo "kept: $EVIDENCE/kept/$1/$(basename "$2")"
}

stop_pid() {  # stop a pid we recorded, only if its cmdline still matches what we launched
  local file="$1" pattern="$2" pid
  [ -f "$file" ] || return 0
  pid="$(cat "$file")"
  if alive "$pid" && tr '\0' ' ' </proc/"$pid"/cmdline | grep -q -- "$pattern"; then
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true   # setsid => pid is the group leader
    for _ in $(seq 1 20); do alive "$pid" || break; sleep 0.25; done
    alive "$pid" && kill -KILL -- "-$pid" 2>/dev/null || true
    echo "stopped $(basename "$file" .pid) pid=$pid"
  fi
}

cmd_cleanup() {
  [ -d "$SCRATCH" ] || { echo "nothing to clean for RUN_ID=$RUN_ID"; echo "evidence kept: $EVIDENCE"; return 0; }
  mkdir -p "$EVIDENCE"
  stop_pid "$SCRATCH/console.pid" "vite.js"
  stop_pid "$SCRATCH/engine.pid" "$ROOT/.venv/bin/vox-engine start"
  [ -f "$SCRATCH/engine.log" ] && cp "$SCRATCH/engine.log" "$EVIDENCE/engine-final.log"
  [ -f "$SCRATCH/console.log" ] && cp "$SCRATCH/console.log" "$EVIDENCE/console-final.log"
  rm -rf "$SCRATCH"
  echo "evidence kept: $EVIDENCE"
}

case "${1:-}" in
  setup|launch|doctor|env|cli|api|wav|db|ui|keep|cleanup) c="$1"; shift; "cmd_$c" "$@" ;;
  *) sed -n '2,19p' "$0"; exit 2 ;;
esac
