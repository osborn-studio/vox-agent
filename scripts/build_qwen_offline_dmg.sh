#!/usr/bin/env bash
# Add the official Qwen3-ASR-0.6B weights and a self-contained MLX runtime to an existing app.
set -euo pipefail
: "${SOURCE_APP:?Path to a verified Vox Agent.app}"
: "${QWEN_MODEL_DIR:?Path to the official Qwen3-ASR-0.6B snapshot}"
: "${QWEN_PYTHON:?Python environment with mlx-qwen3-asr and PyInstaller}"
: "${OUTPUT_DIR:?New, empty output directory (allow several GB)}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ ! -e "$OUTPUT_DIR" ]] || { echo 'OUTPUT_DIR must not exist' >&2; exit 1; }
[[ -f "$SOURCE_APP/Contents/MacOS/VoxLog" ]] || exit 1
EXPECTED_MODEL_SHA=79d6cbd4c98c7bbffe9db2edac07f56cd6637d0d5944b27f6c2b8353840323ea
[[ "$(shasum -a 256 "$QWEN_MODEL_DIR/model.safetensors" | awk '{print $1}')" == "$EXPECTED_MODEL_SHA" ]] || { echo 'Model checksum mismatch' >&2; exit 1; }
for required in config.json tokenizer_config.json preprocessor_config.json vocab.json merges.txt LICENSE SOURCE.txt; do
  [[ -s "$QWEN_MODEL_DIR/$required" ]] || { echo "Missing model file: $required" >&2; exit 1; }
done
mkdir -p "$OUTPUT_DIR/staging"
APP="$OUTPUT_DIR/staging/Vox Agent.app"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$SOURCE_APP/Contents/Info.plist")"
ditto "$SOURCE_APP" "$APP"
"$QWEN_PYTHON" -m PyInstaller --noconfirm --clean --onefile \
  --name qwen3-asr-server --distpath "$OUTPUT_DIR/runtime" \
  --workpath "$OUTPUT_DIR/work" --specpath "$OUTPUT_DIR" \
  --collect-all mlx --collect-all mlx_qwen3_asr --collect-all uvicorn \
  --collect-all soundfile --collect-all websockets \
  "$ROOT_DIR/macos/qwen3-asr-server/server.py"
# The native 4.2.1 loader expects a sibling executable in Contents/MacOS.
cp "$OUTPUT_DIR/runtime/qwen3-asr-server" "$APP/Contents/MacOS/"
mkdir -p "$APP/Contents/Resources/Models"
ditto "$QWEN_MODEL_DIR" "$APP/Contents/Resources/Models/Qwen3-ASR"
"$QWEN_PYTHON" -m pip freeze > "$APP/Contents/Resources/QWEN_RUNTIME_VERSIONS.txt"
# Preserve runtime dependency license files from installed distribution metadata.
"$QWEN_PYTHON" - "$APP/Contents/Resources/QwenRuntimeLicenses" <<'PY'
import importlib.metadata as m, pathlib, shutil, sys
root=pathlib.Path(sys.argv[1]); root.mkdir()
for dist in m.distributions():
    for file in dist.files or []:
        name=str(file)
        if '.dist-info/' in name and any(word in name.lower() for word in ('license','copying','notice')):
            source=pathlib.Path(dist.locate_file(file))
            if source.is_file():
                target=root / name
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,target)
PY
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
ln -s /Applications "$OUTPUT_DIR/staging/Applications"
hdiutil create -volname "Vox Agent $VERSION Offline" -srcfolder "$OUTPUT_DIR/staging" \
  -format UDZO "$OUTPUT_DIR/VoxAgent-v$VERSION-qwen3-asr-0.6B-arm64.dmg"
(cd "$OUTPUT_DIR" && shasum -a 256 "VoxAgent-v$VERSION-qwen3-asr-0.6B-arm64.dmg" > QWEN-SHA256SUMS.txt)
echo "Offline package: $OUTPUT_DIR"
