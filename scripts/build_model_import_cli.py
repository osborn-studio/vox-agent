"""Build the offline, revision-pinned import/verify utility. Does not import data."""

import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = root / "build/model-import"
    out.mkdir(parents=True, exist_ok=True)
    files = [
        "ModelManifest",
        "ModelTreeVerifier",
        "ModelStore",
        "QwenModelCatalog",
        "ASRComponentResolver",
    ]
    subprocess.run(
        [
            "swiftc",
            "-D",
            "MODEL_IMPORT_CLI",
            "-sdk",
            args.sdk,
            "-parse-as-library",
            "-module-cache-path",
            str(out / "module-cache"),
            *[str(root / "macos/VoxLog/Services" / (name + ".swift")) for name in files],
            str(root / "scripts/ModelImportCLI.swift"),
            "-o",
            str(out / "model-import"),
        ],
        check=True,
        timeout=90,
    )
    print(out / "model-import")


if __name__ == "__main__":
    main()
