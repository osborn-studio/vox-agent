#!/usr/bin/env python3
"""Inspect, back up, and temporary-restore VoxLog/Vox Agent release data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.release_data_preflight import (  # noqa: E402
    build_release_preflight,
    create_release_backup,
    restore_release_backup,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create secret-free upgrade evidence and an allowlisted rollback backup. "
            "No Keychain item, credentials file, model, cache, or log is copied."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect = subparsers.add_parser("inspect", help="read-only preflight")
    _add_source_arguments(inspect)
    inspect.add_argument(
        "--output",
        type=Path,
        help="write the secret-free JSON manifest instead of printing it",
    )

    backup = subparsers.add_parser("backup", help="create an allowlisted rollback backup")
    _add_source_arguments(backup)
    backup.add_argument(
        "--backup-root",
        type=Path,
        default=Path.home() / "Documents" / "Vox Agent Backups",
        help="directory outside all VoxLog/Vox Agent data roots",
    )
    backup.add_argument("--backup-id", help="optional stable identifier for automation")

    restore = subparsers.add_parser(
        "restore-test",
        help="restore and verify into an empty temporary home",
    )
    restore.add_argument("--manifest", type=Path, required=True)
    restore.add_argument("--restore-home", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "inspect":
        payload = build_release_preflight(
            home=args.home,
            canonical_data_dir=args.canonical_data_dir,
        )
        content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
            print(args.output.expanduser().resolve())
        else:
            print(content, end="")
        return 0

    if args.command == "backup":
        manifest = create_release_backup(
            home=args.home,
            canonical_data_dir=args.canonical_data_dir,
            backup_root=args.backup_root,
            backup_id=args.backup_id,
        )
        print(manifest)
        return 0

    result = restore_release_backup(
        manifest_path=args.manifest,
        restore_home=args.restore_home,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
        help="owner home containing legacy VoxLog paths",
    )
    parser.add_argument(
        "--canonical-data-dir",
        type=Path,
        help="override the canonical Vox Agent data root",
    )


if __name__ == "__main__":
    raise SystemExit(main())
