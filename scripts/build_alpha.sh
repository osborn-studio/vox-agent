#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/build"
APP_BUNDLE="$BUILD_DIR/VoxLog2.app"
APP_BIN_RELEASE="$ROOT_DIR/apps/desktop-tauri/src-tauri/target/release/vox-agent-desktop-tauri"
APP_ICON="$ROOT_DIR/apps/desktop-tauri/src-tauri/icons/icon.png"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
CARGO_BIN="${CARGO_BIN:-${HOME}/.cargo/bin/cargo}"
NODE_BIN="$(command -v node || true)"
PYINSTALLER_DIST_DIR="$BUILD_DIR/pyinstaller"
RAW_ARCH="${BUILD_ARCH:-$(uname -m)}"

case "$RAW_ARCH" in
  arm64|aarch64)
    ARCH_SLUG="arm64"
    ;;
  x86_64|amd64)
    ARCH_SLUG="intel"
    ;;
  *)
    echo "unsupported build arch: $RAW_ARCH" >&2
    exit 1
    ;;
esac

MACOS_DIST_DIR="$DIST_DIR/voxlog2-alpha-macos-$ARCH_SLUG"
INSTALLER_DIR="$DIST_DIR/voxlog2-alpha-installer-$ARCH_SLUG"
DMG_PATH="$DIST_DIR/VoxLog2-Alpha-$ARCH_SLUG.dmg"
RUNTIME_BUNDLE_DIR="$INSTALLER_DIR/runtime-bundle"

require_command() {
  local command_name="$1"
  local install_hint="$2"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "missing dependency: $command_name" >&2
    echo "$install_hint" >&2
    exit 1
  fi
}

copy_path() {
  local source_path="$1"
  local destination_path="$2"
  mkdir -p "$(dirname "$destination_path")"
  rsync -a "$source_path" "$destination_path"
}

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

require_command "$PYTHON_BIN" "Install Python 3.11+."
require_command npm "Install Node.js and npm."
require_command node "Install Node.js."
require_command rsync "rsync is required."
require_command hdiutil "hdiutil is required on macOS."
if ! command -v cargo >/dev/null 2>&1 && [ ! -x "$CARGO_BIN" ]; then
  echo "missing dependency: cargo" >&2
  echo "Install Rust from https://rustup.rs" >&2
  exit 1
fi

mkdir -p "$BUILD_DIR" "$DIST_DIR"

if [ ! -d "$ROOT_DIR/.venv" ]; then
  "$PYTHON_BIN" -m venv "$ROOT_DIR/.venv"
fi

"$ROOT_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$ROOT_DIR/.venv/bin/pip" install -e ".[dev]" >/dev/null
"$ROOT_DIR/.venv/bin/pip" install pyinstaller >/dev/null

cd "$ROOT_DIR"
npm install >/dev/null
npm run -w @voxlog/contracts build
npm run -w @voxlog/api-ts build

rm -rf "$PYINSTALLER_DIST_DIR"
mkdir -p "$PYINSTALLER_DIST_DIR"
(
  cd "$ROOT_DIR"
  "$ROOT_DIR/.venv/bin/pyinstaller" --clean --noconfirm --onefile \
    --distpath "$PYINSTALLER_DIST_DIR" \
    --workpath "$BUILD_DIR/pyinstaller-work-backend" \
    --specpath "$BUILD_DIR/pyinstaller-spec-backend" \
    --paths "$ROOT_DIR" \
    --name voxlog2-backend \
    --collect-all fastapi \
    --collect-all uvicorn \
    --collect-all aiosqlite \
    --collect-all httpx \
    --collect-all structlog \
    --collect-all dotenv \
    --collect-all pydantic \
    scripts/frozen_backend.py >/dev/null
  "$ROOT_DIR/.venv/bin/pyinstaller" --clean --noconfirm --onefile \
    --distpath "$PYINSTALLER_DIST_DIR" \
    --workpath "$BUILD_DIR/pyinstaller-work-gateway" \
    --specpath "$BUILD_DIR/pyinstaller-spec-gateway" \
    --paths "$ROOT_DIR" \
    --name voxlog2-gateway \
    --collect-all fastapi \
    --collect-all uvicorn \
    --collect-all httpx \
    --collect-all pydantic \
    scripts/frozen_gateway.py >/dev/null
)

if command -v cargo >/dev/null 2>&1; then
  cargo build --release --manifest-path "$ROOT_DIR/apps/desktop-tauri/src-tauri/Cargo.toml"
else
  "$CARGO_BIN" build --release --manifest-path "$ROOT_DIR/apps/desktop-tauri/src-tauri/Cargo.toml"
fi

if [ ! -f "$APP_BIN_RELEASE" ]; then
  echo "release binary missing: $APP_BIN_RELEASE" >&2
  exit 1
fi

rm -rf "$APP_BUNDLE" "$MACOS_DIST_DIR" "$INSTALLER_DIR" "$DMG_PATH"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources" "$MACOS_DIST_DIR/bin" "$RUNTIME_BUNDLE_DIR"

cp "$APP_BIN_RELEASE" "$APP_BUNDLE/Contents/MacOS/VoxLog2"
chmod +x "$APP_BUNDLE/Contents/MacOS/VoxLog2"

if [ -f "$APP_ICON" ]; then
  cp "$APP_ICON" "$APP_BUNDLE/Contents/Resources/icon.png"
fi

cat > "$APP_BUNDLE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>VoxLog2</string>
  <key>CFBundleIdentifier</key>
  <string>com.voxlog2.desktop</string>
  <key>CFBundleName</key>
  <string>VoxLog2</string>
  <key>CFBundleDisplayName</key>
  <string>VoxLog2</string>
  <key>CFBundleVersion</key>
  <string>0.1.0-alpha</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0-alpha</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>VoxLog2 records your voice for transcription and memory capture.</string>
</dict>
</plist>
PLIST

cp -R "$APP_BUNDLE" "$MACOS_DIST_DIR/VoxLog2.app"

copy_path "$ROOT_DIR/apps/__init__.py" "$RUNTIME_BUNDLE_DIR/apps/"
copy_path "$ROOT_DIR/apps/desktop" "$RUNTIME_BUNDLE_DIR/apps/"
copy_path "$ROOT_DIR/apps/gateway" "$RUNTIME_BUNDLE_DIR/apps/"
copy_path "$ROOT_DIR/core" "$RUNTIME_BUNDLE_DIR/"
copy_path "$ROOT_DIR/runtime" "$RUNTIME_BUNDLE_DIR/"
copy_path "$ROOT_DIR/memory" "$RUNTIME_BUNDLE_DIR/"
copy_path "$ROOT_DIR/integrations" "$RUNTIME_BUNDLE_DIR/"
copy_path "$ROOT_DIR/dictionaries" "$RUNTIME_BUNDLE_DIR/"
copy_path "$ROOT_DIR/packages/contracts" "$RUNTIME_BUNDLE_DIR/packages/"
copy_path "$ROOT_DIR/services/api-ts" "$RUNTIME_BUNDLE_DIR/services/"
copy_path "$ROOT_DIR/node_modules" "$RUNTIME_BUNDLE_DIR/"
copy_path "$ROOT_DIR/pyproject.toml" "$RUNTIME_BUNDLE_DIR/"
copy_path "$ROOT_DIR/package.json" "$RUNTIME_BUNDLE_DIR/"
copy_path "$ROOT_DIR/package-lock.json" "$RUNTIME_BUNDLE_DIR/"
copy_path "$ROOT_DIR/.env.example" "$RUNTIME_BUNDLE_DIR/"
copy_path "$ROOT_DIR/README.md" "$RUNTIME_BUNDLE_DIR/"
mkdir -p "$RUNTIME_BUNDLE_DIR/bin"
cp "$NODE_BIN" "$RUNTIME_BUNDLE_DIR/bin/node"
cp "$PYINSTALLER_DIST_DIR/voxlog2-backend" "$RUNTIME_BUNDLE_DIR/bin/voxlog2-backend"
cp "$PYINSTALLER_DIST_DIR/voxlog2-gateway" "$RUNTIME_BUNDLE_DIR/bin/voxlog2-gateway"
chmod +x "$RUNTIME_BUNDLE_DIR/bin/node"
chmod +x "$RUNTIME_BUNDLE_DIR/bin/voxlog2-backend" "$RUNTIME_BUNDLE_DIR/bin/voxlog2-gateway"

rm -rf "$RUNTIME_BUNDLE_DIR/apps/desktop-tauri" \
       "$RUNTIME_BUNDLE_DIR/apps/desktop/__pycache__" \
       "$RUNTIME_BUNDLE_DIR/apps/gateway/__pycache__" \
       "$RUNTIME_BUNDLE_DIR/runtime/__pycache__" \
       "$RUNTIME_BUNDLE_DIR/memory/__pycache__" \
       "$RUNTIME_BUNDLE_DIR/integrations/__pycache__"

cp -R "$APP_BUNDLE" "$INSTALLER_DIR/VoxLog2.app"
cp "$ROOT_DIR/scripts/install_from_bundle.sh" "$INSTALLER_DIR/Install VoxLog2.command"
cp "$ROOT_DIR/scripts/uninstall_installed.sh" "$INSTALLER_DIR/Uninstall VoxLog2.command"
chmod +x "$INSTALLER_DIR/Install VoxLog2.command" "$INSTALLER_DIR/Uninstall VoxLog2.command"
ln -s /Applications "$INSTALLER_DIR/Applications"

cat > "$INSTALLER_DIR/README.txt" <<'TXT'
VoxLog2 alpha installer

1. Drag VoxLog2.app to Applications if you want the app first
2. Double-click "Install VoxLog2.command"
3. Wait for the install and health check to finish
4. Open VoxLog2 from /Applications

This installer copies a local runtime bundle into ~/.voxlog2/runtime-alpha,
reuses the bundled Node runtime, sets up launch agents, and installs VoxLog2.app.
TXT

hdiutil create -volname "VoxLog2 Alpha" -srcfolder "$INSTALLER_DIR" -ov -format UDZO "$DMG_PATH" >/dev/null

cp "$DMG_PATH" "$DIST_DIR/VoxLog2-Alpha.dmg"

echo "alpha app bundle ready at $MACOS_DIST_DIR"
echo "alpha installer ready at $INSTALLER_DIR"
echo "alpha dmg ready at $DMG_PATH"
