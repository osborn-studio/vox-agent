"""Snapshot — save and restore engine state.

Captures a point-in-time snapshot of:
- Active tasks and their status
- Agent configurations
- Loaded skills
- Memory entries
- Recent activity

Useful for debugging, backup, and migration.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.data_paths import resolve_vox_agent_data_dir

from .store import StoreManager
from .agent_memory import AgentMemory
from .skills import SkillLoader
from .agent_config import AgentConfigScanner

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOTS_DIR = resolve_vox_agent_data_dir() / "snapshots"


@dataclass
class Snapshot:
    id: str
    timestamp: str
    tasks: list[dict]
    agents: list[dict]
    skills: list[dict]
    memory: dict[str, list[dict]]  # agent_id → entries
    metadata: dict[str, Any]


class SnapshotManager:
    """Create and restore engine state snapshots."""

    def __init__(
        self,
        store: StoreManager,
        memory: AgentMemory,
        skills: SkillLoader,
        scanner: AgentConfigScanner,
        snapshots_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.memory = memory
        self.skills = skills
        self.scanner = scanner
        self.snapshots_dir = snapshots_dir or (resolve_vox_agent_data_dir() / "snapshots")

    def create(self, label: str = "") -> Snapshot:
        """Create a snapshot of current engine state."""
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        snapshot_id = now.strftime("%Y%m%d-%H%M%S")
        if label:
            snapshot_id += f"-{label}"

        # Collect state
        tasks = self.store.list_tasks()
        agents = [asdict(a) for a in self.scanner.scan()]
        skills_list = [{"name": s.name, "description": s.description, "tools": s.tools}
                       for s in self.skills.load_all()]

        memory_data: dict[str, list[dict]] = {}
        for agent_id in self.memory.list_agents_with_memory():
            entries = self.memory.load(agent_id)
            memory_data[agent_id] = [
                {"content": e.content, "timestamp": e.timestamp, "source": e.source}
                for e in entries
            ]

        snapshot = Snapshot(
            id=snapshot_id,
            timestamp=now.isoformat(),
            tasks=tasks,
            agents=agents,
            skills=skills_list,
            memory=memory_data,
            metadata={
                "label": label,
                "task_count": len(tasks),
                "agent_count": len(agents),
                "skill_count": len(skills_list),
                "memory_agents": len(memory_data),
            },
        )

        # Save to disk
        path = self.snapshots_dir / f"{snapshot_id}.json"
        path.write_text(json.dumps(asdict(snapshot), indent=2, ensure_ascii=False, default=str))
        logger.info("snapshot created: %s (%d tasks, %d agents)", snapshot_id, len(tasks), len(agents))

        return snapshot

    def list_snapshots(self) -> list[dict]:
        """List available snapshots."""
        if not self.snapshots_dir.exists():
            return []
        result = []
        for path in sorted(self.snapshots_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text())
                result.append({
                    "id": data.get("id", path.stem),
                    "timestamp": data.get("timestamp", ""),
                    "metadata": data.get("metadata", {}),
                    "size_bytes": path.stat().st_size,
                })
            except Exception:
                continue
        return result

    def load(self, snapshot_id: str) -> Snapshot | None:
        """Load a snapshot from disk."""
        path = self.snapshots_dir / f"{snapshot_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return Snapshot(**data)
        except Exception:
            logger.exception("failed to load snapshot %s", snapshot_id)
            return None

    def delete(self, snapshot_id: str) -> bool:
        """Delete a snapshot."""
        path = self.snapshots_dir / f"{snapshot_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def cleanup(self, keep_latest: int = 10) -> int:
        """Remove old snapshots, keeping only the latest N."""
        snapshots = sorted(self.snapshots_dir.glob("*.json"), reverse=True)
        removed = 0
        for path in snapshots[keep_latest:]:
            path.unlink()
            removed += 1
        return removed
