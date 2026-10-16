"""Task Pinning — pin important tasks to the top of the list.

Pinned tasks stay visible in the TaskBar regardless of their status.
Used for ongoing projects, recurring reviews, or important references.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)


class PinManager:
    """Manage pinned tasks."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or resolve_vox_agent_data_dir()
        self._pins: set[str] = set()
        self._load()

    def pin(self, task_id: str) -> None:
        self._pins.add(task_id)
        self._save()

    def unpin(self, task_id: str) -> None:
        self._pins.discard(task_id)
        self._save()

    def toggle(self, task_id: str) -> bool:
        """Toggle pin. Returns new state."""
        if task_id in self._pins:
            self._pins.discard(task_id)
            self._save()
            return False
        self._pins.add(task_id)
        self._save()
        return True

    def is_pinned(self, task_id: str) -> bool:
        return task_id in self._pins

    def list_pinned(self) -> list[str]:
        return sorted(self._pins)

    def count(self) -> int:
        return len(self._pins)

    def clear(self) -> int:
        count = len(self._pins)
        self._pins.clear()
        self._save()
        return count

    def _load(self) -> None:
        path = self.config_dir / "pinned.json"
        if path.exists():
            try:
                self._pins = set(json.loads(path.read_text()))
            except Exception:
                self._pins = set()

    def _save(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        path = self.config_dir / "pinned.json"
        path.write_text(json.dumps(sorted(self._pins)))
