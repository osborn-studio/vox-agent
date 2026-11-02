#!/usr/bin/env bash
# Build the runtime only. Models stay in the user's external, verified store.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${QWEN_PYTHON:?Python environment with qwen3-asr-server requirements and PyInstaller}"
: "${QWEN_BUILD_DIR:?New runtime build directory}"
[[ ! -e "$QWEN_BUILD_DIR" ]] || { echo 'QWEN_BUILD_DIR must not exist' >&2; exit 1; }
"$QWEN_PYTHON" -c 'import PyInstaller, mlx.core, mlx_qwen3_asr, llama_cpp, soundfile, fastapi, uvicorn, websockets, numpy'
mkdir -p "$QWEN_BUILD_DIR"
"$QWEN_PYTHON" -m PyInstaller --noconfirm --clean --onefile \
  --name qwen3-asr-server --distpath "$QWEN_BUILD_DIR/dist" \
  --workpath "$QWEN_BUILD_DIR/work" --specpath "$QWEN_BUILD_DIR" \
  --collect-all mlx --collect-all mlx_qwen3_asr --collect-all llama_cpp \
  --collect-all soundfile --collect-all uvicorn --collect-all websockets \
  "$ROOT_DIR/macos/qwen3-asr-server/server.py"
"$QWEN_PYTHON" - "$QWEN_BUILD_DIR" <<'PY'
import importlib.metadata as metadata
from pathlib import Path
import shutil
import sys
root = Path(sys.argv[1])
notices = root / 'QwenRuntimeLicenses'
notices.mkdir()
distributions = list(metadata.distributions())
for dist in distributions:
    for entry in dist.files or []:
        if '.dist-info/' in str(entry) and any(word in str(entry).lower() for word in ('license', 'copying', 'notice')):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                target = notices / entry
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
# Names and versions only: pip freeze can expose private direct-install URLs.
(root / 'QWEN_RUNTIME_VERSIONS.txt').write_text(''.join(sorted(
    f'{dist.metadata["Name"]}=={dist.version}\n' for dist in distributions
)))
PY
# No model download or GPU is needed for the CLI smoke test on hosted runners.
"$QWEN_BUILD_DIR/dist/qwen3-asr-server" --help
