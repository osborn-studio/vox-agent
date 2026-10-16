#!/usr/bin/env bash

set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SOURCE="$BUNDLE_DIR/VoxLog2.app"
RUNTIME_SOURCE="$BUNDLE_DIR/runtime-bundle"
TARGET_HOME="$HOME/.voxlog2"
TARGET_RUNTIME="${VOXLOG_INSTALL_RUNTIME_DIR:-$TARGET_HOME/runtime-alpha}"
TARGET_APP="/Applications/VoxLog2.app"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BIN_DIR="$TARGET_HOME/bin"
ENV_FILE="$TARGET_HOME/.env"
SETTINGS_FILE="$TARGET_HOME/desktop-settings.json"
BACKEND_LOG="$TARGET_HOME/backend.log"
API_TS_LOG="$TARGET_HOME/api-ts.log"
GATEWAY_LOG="$TARGET_HOME/gateway.log"
API_TS_ENTRY_REL="services/api-ts/dist/services/api-ts/src/main.js"
DB_FILE="$TARGET_HOME/history-alpha.db"
BACKEND_BINARY_REL="bin/voxlog2-backend"
GATEWAY_BINARY_REL="bin/voxlog2-gateway"

require_command() {
  local command_name="$1"
  local hint="$2"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "missing dependency: $command_name" >&2
    echo "$hint" >&2
    exit 1
  fi
}

require_command rsync "rsync is required on macOS."
require_command curl "curl is required on macOS."
require_command openssl "openssl is required to generate the local service credential."
require_command security "macOS Keychain access is required for local service credentials."

mkdir -p "$TARGET_HOME" "$BIN_DIR" "$LAUNCH_AGENTS_DIR"

if [ ! -d "$RUNTIME_SOURCE" ]; then
  echo "runtime bundle missing at $RUNTIME_SOURCE" >&2
  exit 1
fi

rsync -a --delete "$RUNTIME_SOURCE/" "$TARGET_RUNTIME/"

NODE_BIN="$TARGET_RUNTIME/bin/node"
if [ ! -x "$NODE_BIN" ]; then
  if command -v node >/dev/null 2>&1; then
    mkdir -p "$TARGET_RUNTIME/bin"
    cp "$(command -v node)" "$NODE_BIN"
    chmod +x "$NODE_BIN"
  else
    echo "bundled Node runtime missing and node is not installed" >&2
    exit 1
  fi
fi

if [ ! -f "$ENV_FILE" ] && [ -f "$TARGET_RUNTIME/.env.example" ]; then
  cp "$TARGET_RUNTIME/.env.example" "$ENV_FILE"
fi

KEYCHAIN_SERVICE="com.voxlog2.desktop"
KEYCHAIN_ACCOUNT="backend_api_token"
KEYCHAIN_TOKEN="$(security find-generic-password -w -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" 2>/dev/null || true)"
if [ -z "$KEYCHAIN_TOKEN" ] && [ -f "$ENV_FILE" ]; then
  KEYCHAIN_TOKEN="$(awk -F= '
    /^[[:space:]]*(export[[:space:]]+)?VOXLOG_API_TOKEN[[:space:]]*=/ {
      value=$0
      sub(/^[^=]*=/, "", value)
      gsub(/^[[:space:]\047\"]+|[[:space:]\047\"]+$/, "", value)
      print value
      exit
    }
  ' "$ENV_FILE")"
  case "$KEYCHAIN_TOKEN" in
    ""|change-me|change-me-to-a-random-string) KEYCHAIN_TOKEN="" ;;
  esac
fi
if [ -z "$KEYCHAIN_TOKEN" ]; then
  KEYCHAIN_TOKEN="$(openssl rand -hex 32)"
fi
security add-generic-password -U -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" -w "$KEYCHAIN_TOKEN" >/dev/null

# Only scrub the legacy plaintext token after Keychain storage succeeds.
if [ -f "$ENV_FILE" ]; then
  awk '!/^[[:space:]]*(export[[:space:]]+)?VOXLOG_API_TOKEN[[:space:]]*=/' \
    "$ENV_FILE" > "$ENV_FILE.migrating"
  chmod 600 "$ENV_FILE.migrating"
  mv "$ENV_FILE.migrating" "$ENV_FILE"
fi
unset KEYCHAIN_TOKEN

if [ ! -f "$SETTINGS_FILE" ]; then
  cat > "$SETTINGS_FILE" <<JSON
{
  "backend_base_url": "http://127.0.0.1:7901",
  "active_profile": "home",
  "digest_enhancement_enabled": true,
  "digest_enhancement_provider": "auto",
  "obsidian_vault_dir": "",
  "ai_mate_memory_dir": "",
  "onboarding_completed": false,
  "hotkey_accelerator": "CommandOrControl+Shift+Space",
  "hotkey_enabled": false,
  "never_archive_apps": [
    "1password",
    "keychain access"
  ],
  "fast_path_only_apps": [
    "cursor",
    "claude"
  ],
  "disable_direct_typing_apps": [
    "terminal",
    "iterm",
    "warp"
  ]
}
JSON
fi

if [ ! -f "$TARGET_RUNTIME/$API_TS_ENTRY_REL" ]; then
  echo "api-ts dist bundle missing; rebuilding with bundled node" >&2
  if [ ! -d "$TARGET_RUNTIME/node_modules" ]; then
    echo "node_modules missing from runtime bundle" >&2
    exit 1
  fi
  (
    cd "$TARGET_RUNTIME"
    PATH="$TARGET_RUNTIME/node_modules/.bin:$PATH" "$NODE_BIN" ./node_modules/typescript/bin/tsc -p packages/contracts/tsconfig.json >/dev/null
    PATH="$TARGET_RUNTIME/node_modules/.bin:$PATH" "$NODE_BIN" ./node_modules/typescript/bin/tsc -p services/api-ts/tsconfig.json >/dev/null
  )
fi

BACKEND_COMMAND="\"\$ROOT_DIR/$BACKEND_BINARY_REL\""
GATEWAY_COMMAND="\"\$ROOT_DIR/$GATEWAY_BINARY_REL\""
if [ ! -x "$TARGET_RUNTIME/$BACKEND_BINARY_REL" ] || [ ! -x "$TARGET_RUNTIME/$GATEWAY_BINARY_REL" ]; then
  if command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="python3.11"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "missing backend runtime: bundled binaries not found and no Python available" >&2
    exit 1
  fi
  if [ ! -d "$TARGET_RUNTIME/.venv" ]; then
    "$PYTHON_BIN" -m venv "$TARGET_RUNTIME/.venv"
  fi
  "$TARGET_RUNTIME/.venv/bin/pip" install --upgrade pip >/dev/null
  "$TARGET_RUNTIME/.venv/bin/pip" install -e "$TARGET_RUNTIME" >/dev/null
  BACKEND_COMMAND="\"\$ROOT_DIR/.venv/bin/python\" -m apps.desktop.server"
  GATEWAY_COMMAND="\"\$ROOT_DIR/.venv/bin/python\" -m uvicorn apps.gateway.server:app --host 127.0.0.1 --port \"\${VOXLOG_GATEWAY_PORT:-7903}\" --no-access-log"
fi

cat > "$BIN_DIR/start-backend.sh" <<SH
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$TARGET_RUNTIME"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ENV_FILE"
  set +a
fi
cd "\$ROOT_DIR"
export VOXLOG_PORT="\${VOXLOG_PORT:-7902}"
export VOXLOG_DB_PATH="$DB_FILE"
export VOXLOG_TERMS_DIR="\$ROOT_DIR/dictionaries"
KEYCHAIN_TOKEN="\$(security find-generic-password -w -s com.voxlog2.desktop -a backend_api_token 2>/dev/null || true)"
export VOXLOG_API_TOKEN="\${KEYCHAIN_TOKEN:-\${VOXLOG_API_TOKEN:-}}"
unset KEYCHAIN_TOKEN
$BACKEND_COMMAND
SH

cat > "$BIN_DIR/start-api-ts.sh" <<SH
#!/usr/bin/env bash
set -euo pipefail
wait_for_backend() {
  local url="\$1"
  local attempts="\${2:-45}"
  for _ in \$(seq 1 "\$attempts"); do
    if curl -sf "\$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}
ROOT_DIR="$TARGET_RUNTIME"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ENV_FILE"
  set +a
fi
PY_BACKEND_URL="\${VOXLOG_PY_BACKEND_URL:-http://127.0.0.1:7902}"
KEYCHAIN_TOKEN="\$(security find-generic-password -w -s com.voxlog2.desktop -a backend_api_token 2>/dev/null || true)"
wait_for_backend "\${PY_BACKEND_URL}/health" 45
cd "\$ROOT_DIR/services/api-ts"
VOXLOG_TS_PORT="\${VOXLOG_TS_PORT:-7901}" \
VOXLOG_PY_BACKEND_URL="\$PY_BACKEND_URL" \
VOXLOG_PY_BACKEND_API_TOKEN="\${VOXLOG_PY_BACKEND_API_TOKEN:-\${KEYCHAIN_TOKEN:-\${VOXLOG_API_TOKEN:-}}}" \
"\$ROOT_DIR/bin/node" "\$ROOT_DIR/$API_TS_ENTRY_REL"
SH

cat > "$BIN_DIR/start-gateway.sh" <<SH
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$TARGET_RUNTIME"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ENV_FILE"
  set +a
fi
cd "\$ROOT_DIR"
export VOXLOG_GATEWAY_UPSTREAM_URL="\${VOXLOG_GATEWAY_UPSTREAM_URL:-http://127.0.0.1:7901}"
$GATEWAY_COMMAND
SH

chmod +x "$BIN_DIR/start-backend.sh" "$BIN_DIR/start-api-ts.sh" "$BIN_DIR/start-gateway.sh"

rm -rf "$TARGET_APP"
cp -R "$APP_SOURCE" "$TARGET_APP"

cat > "$LAUNCH_AGENTS_DIR/com.voxlog2.backend.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.voxlog2.backend</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BIN_DIR/start-backend.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>$TARGET_RUNTIME</string>
  <key>StandardOutPath</key>
  <string>$TARGET_HOME/backend.log</string>
  <key>StandardErrorPath</key>
  <string>$TARGET_HOME/backend.log</string>
</dict>
</plist>
PLIST

cat > "$LAUNCH_AGENTS_DIR/com.voxlog2.api-ts.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.voxlog2.api-ts</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BIN_DIR/start-api-ts.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>$TARGET_RUNTIME</string>
  <key>StandardOutPath</key>
  <string>$TARGET_HOME/api-ts.log</string>
  <key>StandardErrorPath</key>
  <string>$TARGET_HOME/api-ts.log</string>
</dict>
</plist>
PLIST

cat > "$LAUNCH_AGENTS_DIR/com.voxlog2.gateway.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.voxlog2.gateway</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BIN_DIR/start-gateway.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>$TARGET_RUNTIME</string>
  <key>StandardOutPath</key>
  <string>$GATEWAY_LOG</string>
  <key>StandardErrorPath</key>
  <string>$GATEWAY_LOG</string>
</dict>
</plist>
PLIST

launch_agent_reload() {
  local plist_path="$2"
  launchctl unload "$plist_path" >/dev/null 2>&1 || true
  launchctl load "$plist_path" >/dev/null 2>&1 || true
}

wait_for_health() {
  local name="$1"
  local url="$2"
  local log_file="$3"
  local attempts="${4:-60}"
  for _ in $(seq 1 "$attempts"); do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "$name failed health check: $url" >&2
  if [ -f "$log_file" ]; then
    tail -n 60 "$log_file" >&2 || true
  fi
  return 1
}

health_ready() {
  local url="$1"
  curl -sf "$url" >/dev/null 2>&1
}

start_service_now() {
  local script_path="$2"
  local log_file="$3"
  pkill -f "$script_path" >/dev/null 2>&1 || true
  nohup "$script_path" >>"$log_file" 2>&1 &
}

: > "$BACKEND_LOG"
: > "$API_TS_LOG"
: > "$GATEWAY_LOG"

launch_agent_reload "com.voxlog2.backend" "$LAUNCH_AGENTS_DIR/com.voxlog2.backend.plist"
launch_agent_reload "com.voxlog2.api-ts" "$LAUNCH_AGENTS_DIR/com.voxlog2.api-ts.plist"
launch_agent_reload "com.voxlog2.gateway" "$LAUNCH_AGENTS_DIR/com.voxlog2.gateway.plist"

if ! health_ready "http://127.0.0.1:7902/health"; then
  start_service_now "backend" "$BIN_DIR/start-backend.sh" "$BACKEND_LOG"
fi
wait_for_health "backend" "http://127.0.0.1:7902/health" "$BACKEND_LOG" 60
if ! health_ready "http://127.0.0.1:7901/health"; then
  start_service_now "api-ts" "$BIN_DIR/start-api-ts.sh" "$API_TS_LOG"
fi
wait_for_health "api-ts" "http://127.0.0.1:7901/health" "$API_TS_LOG" 60
if ! health_ready "http://127.0.0.1:7903/health"; then
  start_service_now "gateway" "$BIN_DIR/start-gateway.sh" "$GATEWAY_LOG"
fi
wait_for_health "gateway" "http://127.0.0.1:7903/health" "$GATEWAY_LOG" 60

if [ "${VOXLOG_AUTO_OPEN_APP:-1}" = "1" ]; then
  open "$TARGET_APP"
fi

echo "installed VoxLog2 to $TARGET_APP"
echo "runtime installed to $TARGET_RUNTIME"
echo "env file: $ENV_FILE"
echo "settings file: $SETTINGS_FILE"
