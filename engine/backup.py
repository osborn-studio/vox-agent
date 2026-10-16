"""Backup — automated backup of all engine data.

Creates timestamped backup snapshots of:
- SQLite databases (tasks.db, activity.db)
- Configuration files (agents.json, groups.json, etc.)
- Memory files
- Skills
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)


class BackupManager:
    """Create and manage engine data backups."""

    def __init__(
        self,
        data_dir: Path | None = None,
        backup_dir: Path | None = None,
    ) -> None:
        canonical = resolve_vox_agent_data_dir()
        self.data_dir = data_dir or canonical
        self.backup_dir = backup_dir or (canonical / "backups")

    def create(self, label: str = "") -> dict:
        """Create a full backup. Returns backup info."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"backup-{ts}"
        if label:
            name += f"-{label}"
        backup_path = self.backup_dir / name
        backup_path.mkdir()

        files_backed = 0
        total_bytes = 0

        # Backup databases
        for db_name in ["tasks.db", "activity.db", "messages.db"]:
            src = self.data_dir / db_name
            if src.exists():
                self._snapshot_database(src, backup_path / db_name)
                files_backed += 1
                total_bytes += src.stat().st_size

        # Backup config files
        for config_name in ["agents.json", "groups.json", "tasks.json",
                            "openclaw-bindings.json", "settings.json"]:
            src = self.data_dir / config_name
            if src.exists():
                shutil.copy2(src, backup_path / config_name)
                files_backed += 1

        # Backup memory
        memory_dir = self.data_dir / "memory"
        if memory_dir.exists():
            dest = backup_path / "memory"
            shutil.copytree(memory_dir, dest, dirs_exist_ok=True)
            files_backed += sum(1 for _ in dest.rglob("*") if _.is_file())

        # Backup skills
        skills_dir = self.data_dir / "skills"
        if skills_dir.exists():
            dest = backup_path / "skills"
            shutil.copytree(skills_dir, dest, dirs_exist_ok=True)
            files_backed += sum(1 for _ in dest.rglob("*") if _.is_file())

        # Write manifest
        manifest = {
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "files": files_backed,
            "size_bytes": total_bytes,
        }
        (backup_path / "manifest.json").write_text(json.dumps(manifest, indent=2))

        logger.info("backup created: %s (%d files, %d bytes)", name, files_backed, total_bytes)
        return manifest

    def list_backups(self) -> list[dict]:
        """List available backups."""
        if not self.backup_dir.exists():
            return []
        result = []
        for path in sorted(self.backup_dir.iterdir(), reverse=True):
            if not path.is_dir():
                continue
            manifest_path = path / "manifest.json"
            if manifest_path.exists():
                try:
                    result.append(json.loads(manifest_path.read_text()))
                except Exception:
                    result.append({"name": path.name, "timestamp": "", "files": 0})
            else:
                result.append({"name": path.name})
        return result

    def restore(self, backup_name: str) -> bool:
        """Restore from a backup."""
        backup_path = self.backup_dir / backup_name
        if not backup_path.exists():
            return False

        # Restore databases
        for db_name in ["tasks.db", "activity.db", "messages.db"]:
            src = backup_path / db_name
            if src.exists():
                self.data_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, self.data_dir / db_name)

        # Restore config files
        for config_name in ["agents.json", "groups.json", "settings.json"]:
            src = backup_path / config_name
            if src.exists():
                shutil.copy2(src, self.data_dir / config_name)

        # Restore memory
        memory_src = backup_path / "memory"
        if memory_src.exists():
            memory_dest = self.data_dir / "memory"
            memory_dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(memory_src, memory_dest, dirs_exist_ok=True)

        logger.info("restored backup: %s", backup_name)
        return True

    @staticmethod
    def _snapshot_database(source: Path, destination: Path) -> None:
        """Use SQLite's online backup API so committed WAL pages are included."""

        with closing(sqlite3.connect(source, timeout=5)) as source_db:
            source_db.execute("PRAGMA busy_timeout=5000")
            with closing(sqlite3.connect(destination, timeout=5)) as destination_db:
                source_db.backup(destination_db)

    def delete(self, backup_name: str) -> bool:
        backup_path = self.backup_dir / backup_name
        if backup_path.exists():
            shutil.rmtree(backup_path)
            return True
        return False

    def cleanup(self, keep_latest: int = 5) -> int:
        """Remove old backups, keeping only the latest N."""
        backups = sorted(self.backup_dir.iterdir(), reverse=True) if self.backup_dir.exists() else []
        dirs = [b for b in backups if b.is_dir()]
        removed = 0
        for path in dirs[keep_latest:]:
            shutil.rmtree(path)
            removed += 1
        return removed
