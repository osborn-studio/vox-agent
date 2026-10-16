#!/usr/bin/env bash

set -euo pipefail

APP_TARGET="/Applications/VoxLog2.app"
VOXLOG_HOME="$HOME/.voxlog2"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BACKEND_HEALTH_URL="${VOXLOG_BACKEND_HEALTH_URL:-http://127.0.0.1:7902/health}"
TS_HEALTH_URL="${VOXLOG_TS_HEALTH_URL:-http://127.0.0.1:7901/health}"
GATEWAY_HEALTH_URL="${VOXLOG_GATEWAY_HEALTH_URL:-http://127.0.0.1:7903/health}"
ENV_FILE="$VOXLOG_HOME/.env"
SETTINGS_FILE="$VOXLOG_HOME/desktop-settings.json"

check_file() {
  local label="$1"
  local path="$2"
  if [ -e "$path" ]; then
    echo "[ok] $label: $path"
  else
    echo "[missing] $label: $path"
  fi
}

check_http() {
  local label="$1"
  local url="$2"
  if curl -sf "$url" >/dev/null 2>&1; then
    echo "[ok] $label: $url"
  else
    echo "[down] $label: $url"
  fi
}

check_keychain() {
  if security find-generic-password -w -s com.voxlog2.desktop -a backend_api_token \
      >/dev/null 2>&1; then
    echo "[ok] Backend service token: macOS Keychain"
  else
    echo "[missing] Backend service token: macOS Keychain"
  fi
}

check_plaintext_secrets() {
  if [ ! -f "$ENV_FILE" ]; then
    return
  fi
  if grep -Eq '^[[:space:]]*(export[[:space:]]+)?(VOXLOG_API_TOKEN|DASHSCOPE_API_KEY(_CN)?|OPENAI_API_KEY|SILICONFLOW_API_KEY)[[:space:]]*=' "$ENV_FILE"; then
    echo "[warning] Plaintext secrets remain in $ENV_FILE"
  else
    echo "[ok] Env file contains no managed plaintext secrets"
  fi
}

echo "VoxLog2 alpha doctor"
check_file "Desktop app" "$APP_TARGET"
check_file "Env file" "$ENV_FILE"
check_file "Desktop settings" "$SETTINGS_FILE"
check_file "Backend launch agent" "$LAUNCH_AGENTS_DIR/com.voxlog2.backend.plist"
check_file "API TS launch agent" "$LAUNCH_AGENTS_DIR/com.voxlog2.api-ts.plist"
check_file "Gateway launch agent" "$LAUNCH_AGENTS_DIR/com.voxlog2.gateway.plist"
check_file "Bundled Node runtime" "$VOXLOG_HOME/runtime-alpha/bin/node"
check_file "Backend log" "$VOXLOG_HOME/backend.log"
check_file "API TS log" "$VOXLOG_HOME/api-ts.log"
check_file "Gateway log" "$VOXLOG_HOME/gateway.log"
check_keychain
check_plaintext_secrets
check_http "Backend health" "$BACKEND_HEALTH_URL"
check_http "API TS health" "$TS_HEALTH_URL"
check_http "Gateway health" "$GATEWAY_HEALTH_URL"
