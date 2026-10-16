"""Dry-run, backup, execute, and rollback for split org-collab pilot data."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .org_schema import (
    ACCESS_AUDIT_COLUMNS,
    ORG_DATABASE_NAME,
    WORK_EVENT_COLUMNS,
    migrate_org_schema,
    open_org_database,
)

_LEGACY_DATABASES = {
    "workEvents": ("work_events.db", "work_event", WORK_EVENT_COLUMNS),
    "accessAudits": ("access_audit.db", "access_audit", ACCESS_AUDIT_COLUMNS),
}


def migrate_org_pilot_data(
    data_dir: Path | str,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Plan or execute an idempotent import into the versioned ``org.db``."""

    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidates = {
        key: _count_legacy_rows(root / filename, table)
        for key, (filename, table, _columns) in _LEGACY_DATABASES.items()
    }
    result: dict[str, Any] = {
        "dryRun": dry_run,
        "executed": False,
        "candidates": candidates,
        "imported": {"workEvents": 0, "accessAudits": 0},
        "manifestPath": None,
    }
    has_legacy_source = any(
        (root / filename).is_file()
        for filename, _table, _columns in _LEGACY_DATABASES.values()
    )
    if dry_run or not has_legacy_source:
        return result

    migration_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"-{uuid4().hex[:8]}"
    )
    backup_dir = root / "org-migration-backups" / migration_id
    backup_dir.mkdir(parents=True, exist_ok=False)
    target = root / ORG_DATABASE_NAME
    target_existed = target.exists()
    target_backup = backup_dir / ORG_DATABASE_NAME if target_existed else None
    source_backups: dict[str, str] = {}
    archived_sources: dict[str, str] = {}

    try:
        for filename, _table, _columns in _LEGACY_DATABASES.values():
            _checkpoint(root / filename)
        if target_existed:
            _checkpoint(target)
            shutil.copy2(target, target_backup)
        for key, (filename, _table, _columns) in _LEGACY_DATABASES.items():
            source = root / filename
            if not source.exists():
                continue
            backup = backup_dir / filename
            shutil.copy2(source, backup)
            source_backups[key] = str(backup)

        imported = _import_sources(root, candidates)

        for key, (filename, _table, _columns) in _LEGACY_DATABASES.items():
            source = root / filename
            if not source.exists():
                continue
            archived = root / f"{filename}.migrated-{migration_id}"
            source.replace(archived)
            archived_sources[key] = str(archived)

        manifest = {
            "version": 1,
            "migrationId": migration_id,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "dataDir": str(root),
            "targetPath": str(target),
            "targetExisted": target_existed,
            "targetBackup": str(target_backup) if target_backup else None,
            "sourceBackups": source_backups,
            "archivedSources": archived_sources,
            "candidates": candidates,
            "imported": imported,
            "rolledBackAt": None,
        }
        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result.update(
            executed=True,
            imported=imported,
            manifestPath=str(manifest_path),
        )
        return result
    except Exception:
        _restore_target_after_failure(target, target_backup, target_existed)
        raise


def rollback_org_pilot_data(manifest_path: Path | str) -> dict[str, Any]:
    """Restore the exact pre-import target and legacy source database files."""

    manifest_file = Path(manifest_path).expanduser().resolve()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    root = Path(manifest["dataDir"]).resolve()
    target = _validated_database_path(root, manifest["targetPath"], ORG_DATABASE_NAME)
    target_existed = bool(manifest["targetExisted"])
    target_backup_raw = manifest.get("targetBackup")
    target_backup = (
        _validated_backup_path(manifest_file, target_backup_raw, ORG_DATABASE_NAME)
        if target_backup_raw
        else None
    )

    _remove_sqlite_family(target)
    if target_existed:
        if target_backup is None or not target_backup.is_file():
            raise RuntimeError("org migration target backup is missing")
        shutil.copy2(target_backup, target)

    for key, (filename, _table, _columns) in _LEGACY_DATABASES.items():
        backup_raw = manifest.get("sourceBackups", {}).get(key)
        if backup_raw is None:
            continue
        backup = _validated_backup_path(manifest_file, backup_raw, filename)
        if not backup.is_file():
            raise RuntimeError(f"org migration source backup is missing: {filename}")
        destination = _validated_database_path(root, root / filename, filename)
        _remove_sqlite_family(destination)
        shutil.copy2(backup, destination)
        archived_raw = manifest.get("archivedSources", {}).get(key)
        if archived_raw:
            archived = Path(archived_raw).resolve()
            if archived.parent == root and archived.name.startswith(f"{filename}.migrated-"):
                archived.unlink(missing_ok=True)

    manifest["rolledBackAt"] = datetime.now(timezone.utc).isoformat()
    manifest_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"restored": True, "manifestPath": str(manifest_file)}


def _import_sources(root: Path, candidates: dict[str, int]) -> dict[str, int]:
    target = open_org_database(root)
    try:
        migrate_org_schema(target)
        imported: dict[str, int] = {}
        for key, (filename, table, columns) in _LEGACY_DATABASES.items():
            source = root / filename
            if not source.exists() or candidates[key] == 0:
                imported[key] = 0
                continue
            before = int(target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            target.execute("ATTACH DATABASE ? AS legacy", (str(source),))
            try:
                column_list = ", ".join(columns)
                target.execute(
                    f"INSERT OR IGNORE INTO {table} ({column_list}) "
                    f"SELECT {column_list} FROM legacy.{table}"
                )
                target.commit()
            finally:
                target.execute("DETACH DATABASE legacy")
            after = int(target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            imported[key] = after - before
        return imported
    finally:
        target.close()


def _count_legacy_rows(path: Path, table: str) -> int:
    if not path.is_file():
        return 0
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if not exists:
                return 0
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"cannot inspect legacy org database: {path.name}") from exc


def _checkpoint(path: Path) -> None:
    if not path.is_file():
        return
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _restore_target_after_failure(
    target: Path,
    target_backup: Path | None,
    target_existed: bool,
) -> None:
    _remove_sqlite_family(target)
    if target_existed and target_backup and target_backup.is_file():
        shutil.copy2(target_backup, target)


def _remove_sqlite_family(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)


def _validated_database_path(root: Path, raw_path: str | Path, filename: str) -> Path:
    resolved = Path(raw_path).resolve()
    if resolved.parent != root or resolved.name != filename:
        raise RuntimeError("org migration manifest contains an unsafe database path")
    return resolved


def _validated_backup_path(
    manifest_path: Path,
    raw_path: str | Path,
    filename: str,
) -> Path:
    resolved = Path(raw_path).resolve()
    if resolved.parent != manifest_path.parent or resolved.name != filename:
        raise RuntimeError("org migration manifest contains an unsafe backup path")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if args.rollback:
        output = rollback_org_pilot_data(args.rollback)
    else:
        if args.data_dir is None:
            parser.error("--data-dir is required unless --rollback is used")
        output = migrate_org_pilot_data(args.data_dir, dry_run=not args.execute)
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
