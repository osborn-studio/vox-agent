#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MACOS_DIR="$ROOT_DIR/macos"
DIST_DIR="$ROOT_DIR/dist"
if [[ -e "$DIST_DIR/voxagent-updates.json" || -L "$DIST_DIR/voxagent-updates.json" ]]; then
  echo "Legacy updater manifest must not be published; archive it before building" >&2
  exit 1
fi
BUILD_FLAVOR="${BUILD_FLAVOR:-voxagent-release}"
if [[ ! "$BUILD_FLAVOR" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "BUILD_FLAVOR must be a simple directory name" >&2
  exit 1
fi
BUILD_DIR="$ROOT_DIR/build/$BUILD_FLAVOR"
APP_NAME="Vox Agent"
APP_BUNDLE="$BUILD_DIR/$APP_NAME.app"
PYINSTALLER_DIST_DIR="$BUILD_DIR/python-dist"
PYTHON_BIN="${PYTHON_BIN:-python3}"
QWEN_PYTHON="${QWEN_PYTHON:-$PYTHON_BIN}"

VERSION="${VERSION:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$MACOS_DIR/VoxLog/Resources/Info.plist")"
fi
BUILD_NUMBER="${BUILD_NUMBER:-$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$MACOS_DIR/VoxLog/Resources/Info.plist")}"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || [[ ! "$BUILD_NUMBER" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]]; then
  echo "VERSION must be x.y.z; BUILD_NUMBER must contain one to three numeric components" >&2
  exit 1
fi

DMG_NAME="VoxAgent-v${VERSION}-cloud.dmg"
DMG_PATH="$DIST_DIR/$DMG_NAME"

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "missing dependency: $name" >&2
    exit 1
  fi
}

# Public configuration only; app secrets must never enter the bundle.
if [ -n "${VOX_TEAM_LOGIN_URL:-}" ]; then
  "$PYTHON_BIN" - <<'PYURL'
import os
from urllib.parse import urlsplit
u = urlsplit(os.environ["VOX_TEAM_LOGIN_URL"])
if (u.scheme != "https" or not u.hostname or u.username or u.password
        or u.query or u.fragment or u.path not in ("", "/")):
    raise SystemExit("VOX_TEAM_LOGIN_URL must be a credential-free HTTPS origin")
PYURL
fi

require_command swift
require_command hdiutil
require_command rsync
require_command "$PYTHON_BIN"

if ! "$PYTHON_BIN" -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "missing Python build dependency: PyInstaller" >&2
  echo "run: $PYTHON_BIN -m pip install . pyinstaller" >&2
  exit 1
fi

# Fail before replacing build outputs if the Qwen build environment is incomplete.
"$QWEN_PYTHON" -c 'import PyInstaller, mlx.core, mlx_qwen3_asr, llama_cpp, soundfile, fastapi, uvicorn, websockets, numpy'

rm -rf "$BUILD_DIR" "$DMG_PATH"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources" "$DIST_DIR"

(
  cd "$MACOS_DIR"
  SWIFT_ARGS=(-c release)
  if [ -n "${MACOS_SDK:-}" ]; then
    SWIFT_ARGS+=(--sdk "$MACOS_SDK")
  fi
  swift build "${SWIFT_ARGS[@]}"
)

(
  cd "$ROOT_DIR"
  "$PYTHON_BIN" -m PyInstaller --clean --noconfirm --onefile \
    --distpath "$PYINSTALLER_DIST_DIR" \
    --workpath "$BUILD_DIR/pyinstaller-work" \
    --specpath "$BUILD_DIR/pyinstaller-spec" \
    --paths "$ROOT_DIR" \
    --name voxagent-engine \
    --collect-all fastapi \
    --collect-all uvicorn \
    --collect-all websockets \
    --collect-all aiosqlite \
    --collect-all httpx \
    --collect-all structlog \
    --collect-all dotenv \
    --collect-all pydantic \
    --collect-all multipart \
    scripts/frozen_engine.py >/dev/null
)

cp "$MACOS_DIR/.build/release/VoxLog" "$APP_BUNDLE/Contents/MacOS/VoxLog"
chmod +x "$APP_BUNDLE/Contents/MacOS/VoxLog"
cp "$MACOS_DIR/VoxLog/Resources/Info.plist" "$APP_BUNDLE/Contents/Info.plist"
if [ -n "${VOX_TEAM_LOGIN_URL:-}" ]; then
  "$PYTHON_BIN" - "$APP_BUNDLE/Contents/Info.plist" <<'PYURL'
import os, plistlib, sys
path = sys.argv[1]
with open(path, "rb") as f:
    info = plistlib.load(f)
info["VoxTeamLoginURL"] = os.environ["VOX_TEAM_LOGIN_URL"].rstrip("/")
with open(path, "wb") as f:
    plistlib.dump(info, f)
PYURL
fi
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP_BUNDLE/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$APP_BUNDLE/Contents/Info.plist"
cp "$MACOS_DIR/VoxLog/Resources/AppIcon.icns" "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
mkdir -p "$APP_BUNDLE/Contents/Resources/bin"
cp "$PYINSTALLER_DIST_DIR/voxagent-engine" "$APP_BUNDLE/Contents/Resources/bin/voxagent-engine"
chmod +x "$APP_BUNDLE/Contents/Resources/bin/voxagent-engine"

if [ -d "$MACOS_DIR/VoxLog/Resources/Sounds" ]; then
  rsync -a "$MACOS_DIR/VoxLog/Resources/Sounds" "$APP_BUNDLE/Contents/Resources/"
fi
if [ -f "$MACOS_DIR/VoxLog/Resources/THIRD_PARTY_LICENSES.txt" ]; then
  cp "$MACOS_DIR/VoxLog/Resources/THIRD_PARTY_LICENSES.txt" "$APP_BUNDLE/Contents/Resources/"
fi

# Every standard upgrade must retain local ASR capability, even without bundled weights.
QWEN_PYTHON="$QWEN_PYTHON" QWEN_BUILD_DIR="$BUILD_DIR/qwen-runtime" \
  bash "$ROOT_DIR/scripts/build_qwen_runtime.sh"
cp "$BUILD_DIR/qwen-runtime/dist/qwen3-asr-server" "$APP_BUNDLE/Contents/MacOS/"
cp "$BUILD_DIR/qwen-runtime/QWEN_RUNTIME_VERSIONS.txt" "$APP_BUNDLE/Contents/Resources/"
cp -R "$BUILD_DIR/qwen-runtime/QwenRuntimeLicenses" "$APP_BUNDLE/Contents/Resources/"

# Standard updates retain external model caches; fail rather than silently strip weights.
"$PYTHON_BIN" "$ROOT_DIR/scripts/write_app_model_catalog.py" --catalog-source "$MACOS_DIR/VoxLog/Services/QwenModelCatalog.swift" --app "$APP_BUNDLE"
"$PYTHON_BIN" "$ROOT_DIR/scripts/verify_small_app_bundle.py" --app "$APP_BUNDLE" --require-qwen-runtime

codesign --force --deep --sign "${CODESIGN_IDENTITY:--}" "$APP_BUNDLE"
codesign --verify --deep --strict "$APP_BUNDLE"

STAGING_DIR="$BUILD_DIR/dmg"
mkdir -p "$STAGING_DIR"
cp -R "$APP_BUNDLE" "$STAGING_DIR/"
ln -s /Applications "$STAGING_DIR/Applications"

hdiutil create \
  -volname "$APP_NAME $VERSION" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH" >/dev/null

"$PYTHON_BIN" "$ROOT_DIR/scripts/verify_release_dmg.py" --dmg "$DMG_PATH"

# A legacy manifest can route old in-place installers to a small App and
# discard bundled model data. Never generate it for new releases.
"$PYTHON_BIN" "$ROOT_DIR/scripts/verify_update_assets.py" \
  --dist "$DIST_DIR" --version "$VERSION" --write-checksum

echo "dmg=$DMG_PATH"
echo "checksums=$DIST_DIR/SHA256SUMS.txt"
