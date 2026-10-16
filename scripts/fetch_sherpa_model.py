"""Install the SenseVoice ASR bundle and Silero VAD model into the verified cache.

Usage: .venv/bin/python scripts/fetch_sherpa_model.py [--dest DIR] [--from FOLDER]

Only manifests pinned under engine/model_manifests/ are installed. Every
download is checked against the pinned sizes and SHA-256 digests before it is
activated; a verified revision is reused without downloading. Without a pinned
manifest the script refuses instead of trusting whatever the URL serves (pin
one with scripts/pin_model_manifest.py).

Offline import (T32): `--from FOLDER` installs from files copied there (same
checks). Files that already exist locally with identical bytes, such as the
unverified bundle older versions downloaded, are reused instead of downloaded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.model_cache import ModelCacheError, ModelManifest, http_fetch, install, local_fetcher  # noqa: E402
from runtime.local_asr_paths import (  # noqa: E402
    SENSEVOICE_CACHE_ID,
    SHERPA_BUNDLE,
    SILERO_VAD_CACHE_ID,
    legacy_voxlog_models_root,
    vox_agent_models_root,
)

MANIFESTS_DIR = ROOT / "engine" / "model_manifests"
REQUIRED = (SENSEVOICE_CACHE_ID,)
OPTIONAL = (SILERO_VAD_CACHE_ID,)


def legacy_model_dirs(dest: Path) -> list[Path]:
    """Unverified model folders older versions wrote (reused only when every
    byte matches the pinned manifest; never moved or changed)."""
    roots = [dest, vox_agent_models_root(), legacy_voxlog_models_root()]
    return [folder for root in roots for folder in (root / SHERPA_BUNDLE, root)]


def main(argv: list[str] | None = None, *, manifests_dir: Path = MANIFESTS_DIR, fetch=http_fetch,
         legacy_dirs: list[Path] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", default=str(Path.home() / ".voxagent" / "models"))
    parser.add_argument("--from", dest="offline", type=Path,
                        help="offline import: install from files already copied into this folder")
    args = parser.parse_args(argv)

    dest = Path(args.dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    if args.offline is not None:
        fetch = local_fetcher(args.offline.expanduser())
    reuse = legacy_model_dirs(dest) if legacy_dirs is None else legacy_dirs
    for model_id in REQUIRED + OPTIONAL:
        path = Path(manifests_dir) / f"{model_id}.json"
        if not path.is_file():
            if model_id in REQUIRED:
                print(f"no pinned manifest for {model_id} ({path}); refusing an unverified download", file=sys.stderr)
                return 1
            print(f"no pinned manifest for {model_id}; skipped")
            continue
        try:
            manifest = ModelManifest.parse(path.read_text(encoding="utf-8"))
            if manifest.id != model_id:
                raise ModelCacheError(f"manifest id {manifest.id} does not match its file name")
            result = install(manifest, dest, fetch, reuse_dirs=reuse)
        except (ModelCacheError, OSError) as exc:
            print(f"{model_id}: install failed, nothing activated: {exc}", file=sys.stderr)
            return 1
        state = ("already verified" if result.reused
                 else f"installed ({result.downloaded} downloaded, {result.reused_files} reused from local copies)")
        print(f"{model_id} {manifest.revision}: {state}")
    print(f"models ready under {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
