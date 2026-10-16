#!/usr/bin/env bash

set -euo pipefail

TARGET_HOME="$HOME/.voxlog2"
TARGET_RUNTIME="${VOXLOG_INSTALL_RUNTIME_DIR:-$TARGET_HOME/runtime-alpha}"
TARGET_APP="/Applications/VoxLog2.app"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BIN_DIR="$TARGET_HOME/bin"

for label in com.voxlog2.backend com.voxlog2.api-ts com.voxlog2.gateway; do
  launchctl bootout "gui/$(id -u)" "$LAUNCH_AGENTS_DIR/$label.plist" >/dev/null 2>&1 || true
  launchctl unload "$LAUNCH_AGENTS_DIR/$label.plist" >/dev/null 2>&1 || true
done

rm -f "$LAUNCH_AGENTS_DIR/com.voxlog2.backend.plist" \
      "$LAUNCH_AGENTS_DIR/com.voxlog2.api-ts.plist" \
      "$LAUNCH_AGENTS_DIR/com.voxlog2.gateway.plist"
rm -f "$BIN_DIR/start-backend.sh" "$BIN_DIR/start-api-ts.sh" "$BIN_DIR/start-gateway.sh"
rm -rf "$TARGET_APP" "$TARGET_RUNTIME"

echo "removed VoxLog2 runtime from $TARGET_RUNTIME"
