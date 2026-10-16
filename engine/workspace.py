"""Workspace Manager — isolated working directories for agent tasks.

Each task gets its own workspace directory for file operations.
Prevents agents from interfering with each other's work.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACES_DIR = resolve_vox_agent_data_dir() / "workspaces"


@dataclass
class WorkspaceInfo:
    task_id: str
    agent_id: str
    path: Path
    created: bool


class WorkspaceManager:
    """Create and manage isolated task workspaces."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (resolve_vox_agent_data_dir() / "workspaces")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create(self, task_id: str, agent_id: str) -> WorkspaceInfo:
        """Create a workspace directory for a task."""
        safe_id = task_id.replace("/", "-")[:20]
        ws_path = self.base_dir / f"{agent_id}_{safe_id}"
        created = not ws_path.exists()
        ws_path.mkdir(parents=True, exist_ok=True)
        logger.info("workspace: %s → %s", task_id[:8], ws_path)
        return WorkspaceInfo(
            task_id=task_id,
            agent_id=agent_id,
            path=ws_path,
            created=created,
        )

    def get_path(self, task_id: str, agent_id: str) -> Path | None:
        """Get workspace path if it exists."""
        safe_id = task_id.replace("/", "-")[:20]
        ws_path = self.base_dir / f"{agent_id}_{safe_id}"
        return ws_path if ws_path.exists() else None

    def cleanup(self, task_id: str, agent_id: str) -> bool:
        """Remove a task's workspace after completion."""
        path = self.get_path(task_id, agent_id)
        if path and path.exists():
            shutil.rmtree(path, ignore_errors=True)
            logger.info("workspace cleaned: %s", path)
            return True
        return False

    def cleanup_all(self, max_age_hours: int = 24) -> int:
        """Remove old workspaces."""
        import time
        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0
        for ws in self.base_dir.iterdir():
            if ws.is_dir() and ws.stat().st_mtime < cutoff:
                shutil.rmtree(ws, ignore_errors=True)
                removed += 1
        if removed:
            logger.info("cleaned %d old workspaces", removed)
        return removed

    def list_active(self) -> list[dict]:
        """List all active workspaces."""
        result = []
        for ws in sorted(self.base_dir.iterdir()):
            if ws.is_dir():
                parts = ws.name.split("_", 1)
                agent = parts[0] if len(parts) > 1 else "unknown"
                task = parts[1] if len(parts) > 1 else ws.name
                size = sum(f.stat().st_size for f in ws.rglob("*") if f.is_file())
                result.append({
                    "agent_id": agent,
                    "task_id": task,
                    "path": str(ws),
                    "size_bytes": size,
                    "file_count": sum(1 for f in ws.rglob("*") if f.is_file()),
                })
        return result

    def disk_usage(self) -> dict:
        """Total disk usage of all workspaces."""
        total = 0
        count = 0
        for ws in self.base_dir.iterdir():
            if ws.is_dir():
                total += sum(f.stat().st_size for f in ws.rglob("*") if f.is_file())
                count += 1
        return {
            "total_bytes": total,
            "total_mb": round(total / 1024 / 1024, 2),
            "workspace_count": count,
        }
