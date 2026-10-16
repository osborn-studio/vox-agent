#!/usr/bin/env python3
"""Read-only update baseline inventory. Never executes helpers or reads secrets."""

import argparse
import json
import os
from pathlib import Path
import plistlib


def inspect_app(app: Path) -> dict:
    if app.is_symlink() or not app.is_dir():
        raise ValueError("Provide an existing non-symlink App directory")
    app = app.resolve()

    def safe_path(relative):
        current = app
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Symlink in bundle path")
        return current

    info_path = safe_path("Contents/Info.plist")
    if info_path.is_symlink() or not info_path.is_file():
        raise ValueError("Missing regular Info.plist")
    with info_path.open("rb") as source:
        info = plistlib.load(source)

    def regular(relative):
        path = safe_path(relative)
        return path.is_file() and not path.is_symlink()

    model = safe_path("Contents/Resources/Models/Qwen3-ASR")
    onefile = regular("Contents/MacOS/qwen3-asr-server")
    server_dist = regular("Contents/Resources/qwen3-asr-server-dist/qwen3-asr-server")
    return {
        "app": str(app),
        "version": info.get("CFBundleShortVersionString"),
        "build": info.get("CFBundleVersion"),
        "bundle_id": info.get("CFBundleIdentifier"),
        "volume_read_only": bool(os.statvfs(app).f_flag & os.ST_RDONLY),
        "bundled_qwen_model_present": model.is_dir() and not model.is_symlink(),
        "helper_layouts": [
            name
            for name, present in [("onefile", onefile), ("server-dist", server_dist)]
            if present
        ],
        "trust_verified": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apps", nargs="+", type=Path)
    args = parser.parse_args()
    apps = [inspect_app(path) for path in args.apps]
    print(
        json.dumps(
            {
                "apps": apps,
                "multiple_copies": len(apps) > 1,
                "auto_selected": False,
                "helpers_executed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
