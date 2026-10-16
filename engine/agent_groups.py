"""Agent Groups — organize agents into teams for batch operations.

Groups enable:
- Broadcast a task to all agents in a group
- Fan-out execution (all agents work on the same task)
- Round-robin assignment (rotate through agents)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)

DEFAULT_GROUPS_PATH = resolve_vox_agent_data_dir() / "groups.json"


@dataclass
class AgentGroup:
    id: str
    name: str
    emoji: str = "📁"
    members: list[str] = field(default_factory=list)
    strategy: str = "round_robin"  # "round_robin" | "broadcast" | "first_available"


class GroupManager:
    """Manage agent groups."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (resolve_vox_agent_data_dir() / "groups.json")
        self._groups: dict[str, AgentGroup] = {}
        self._rr_index: dict[str, int] = {}
        self._load()

    def create(self, group: AgentGroup) -> None:
        self._groups[group.id] = group
        self._save()

    def delete(self, group_id: str) -> bool:
        if self._groups.pop(group_id, None):
            self._save()
            return True
        return False

    def get(self, group_id: str) -> AgentGroup | None:
        return self._groups.get(group_id)

    def list_all(self) -> list[AgentGroup]:
        return list(self._groups.values())

    def add_member(self, group_id: str, agent_id: str) -> bool:
        g = self._groups.get(group_id)
        if not g:
            return False
        if agent_id not in g.members:
            g.members.append(agent_id)
            self._save()
        return True

    def remove_member(self, group_id: str, agent_id: str) -> bool:
        g = self._groups.get(group_id)
        if not g or agent_id not in g.members:
            return False
        g.members.remove(agent_id)
        self._save()
        return True

    def next_agent(self, group_id: str) -> str | None:
        """Get the next agent based on group strategy."""
        g = self._groups.get(group_id)
        if not g or not g.members:
            return None

        if g.strategy == "round_robin":
            idx = self._rr_index.get(group_id, 0)
            agent = g.members[idx % len(g.members)]
            self._rr_index[group_id] = idx + 1
            return agent
        elif g.strategy == "first_available":
            return g.members[0]  # simplified: just pick first
        elif g.strategy == "broadcast":
            return None  # caller should iterate all members

        return g.members[0]

    def broadcast_targets(self, group_id: str) -> list[str]:
        """Get all members for broadcast mode."""
        g = self._groups.get(group_id)
        return list(g.members) if g else []

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            for item in data:
                g = AgentGroup(**item)
                self._groups[g.id] = g
        except Exception:
            logger.exception("failed to load groups")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from dataclasses import asdict
        data = [asdict(g) for g in self._groups.values()]
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
