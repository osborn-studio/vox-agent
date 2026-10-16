#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

npm run -w @voxlog/contracts check
npm run -w @voxlog/api-ts check
npm run -w @voxlog/api-ts test
node --check "$ROOT_DIR/apps/desktop-tauri/src/main.js"

if command -v cargo >/dev/null 2>&1; then
  cargo check --manifest-path "$ROOT_DIR/apps/desktop-tauri/src-tauri/Cargo.toml"
elif [ -x "$HOME/.cargo/bin/cargo" ]; then
  "$HOME/.cargo/bin/cargo" check --manifest-path "$ROOT_DIR/apps/desktop-tauri/src-tauri/Cargo.toml"
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  "$ROOT_DIR/.venv/bin/python" -m pytest \
    "$ROOT_DIR/tests/test_runtime_contracts.py" \
    "$ROOT_DIR/tests/test_recent_utterance.py" \
    "$ROOT_DIR/tests/test_sqlite_store_phase0.py"
fi
