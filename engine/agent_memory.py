"""Agent Memory — cross-session persistent memory (OpenHarness MEMORY.md style).

Each agent gets its own memory file under the Vox Agent data directory.
Memory is loaded into the agent's context on each task execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_DIR = resolve_vox_agent_data_dir() / "memory"


@dataclass
class MemoryEntry:
    key: str
    content: str
    timestamp: str
    source: str = "agent"  # "agent" | "user" | "system"


class AgentMemory:
    """Per-agent persistent memory stored as Markdown files.

    Format:
    ```
    # Agent Memory: claude-code

    ## 2026-04-06T14:30:00Z [agent]
    User prefers pytest over unittest. Always use -v flag.

    ## 2026-04-06T15:00:00Z [user]
    The auth module uses JWT with RS256, not HS256.
    ```
    """

    def __init__(self, memory_dir: Path | None = None) -> None:
        self.memory_dir = memory_dir or (resolve_vox_agent_data_dir() / "memory")
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def get_memory_path(self, agent_id: str) -> Path:
        safe_id = agent_id.replace("/", "-").replace("\\", "-")
        return self.memory_dir / f"{safe_id}.md"

    def load(self, agent_id: str) -> list[MemoryEntry]:
        """Load all memory entries for an agent."""
        path = self.get_memory_path(agent_id)
        if not path.exists():
            return []

        entries = []
        current_key = ""
        current_content_lines: list[str] = []
        current_source = "agent"
        current_ts = ""

        for line in path.read_text().splitlines():
            if line.startswith("## "):
                # save previous entry
                if current_key and current_content_lines:
                    entries.append(MemoryEntry(
                        key=current_key,
                        content="\n".join(current_content_lines).strip(),
                        timestamp=current_ts,
                        source=current_source,
                    ))
                # parse header: ## 2026-04-06T14:30:00Z [agent]
                header = line[3:].strip()
                parts = header.split(" ", 1)
                current_ts = parts[0] if parts else ""
                current_source = "agent"
                if len(parts) > 1 and parts[1].startswith("["):
                    current_source = parts[1].strip("[]")
                current_key = header
                current_content_lines = []
            elif line.startswith("# "):
                continue  # skip title
            elif current_key:
                current_content_lines.append(line)

        # save last entry
        if current_key and current_content_lines:
            entries.append(MemoryEntry(
                key=current_key,
                content="\n".join(current_content_lines).strip(),
                timestamp=current_ts,
                source=current_source,
            ))

        return entries

    def add(self, agent_id: str, content: str, source: str = "agent") -> None:
        """Append a new memory entry."""
        path = self.get_memory_path(agent_id)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not path.exists():
            path.write_text(f"# Agent Memory: {agent_id}\n\n")

        with open(path, "a") as f:
            f.write(f"\n## {ts} [{source}]\n{content}\n")

        logger.info("memory added for %s: %s...", agent_id, content[:50])

    def get_context(self, agent_id: str, max_entries: int = 20) -> str:
        """Get memory as a formatted string for injection into agent context."""
        entries = self.load(agent_id)
        if not entries:
            return ""

        recent = entries[-max_entries:]
        lines = [f"## Agent Memory ({len(recent)} entries)\n"]
        for e in recent:
            lines.append(f"[{e.timestamp}] ({e.source}) {e.content}")
        return "\n".join(lines)

    def clear(self, agent_id: str) -> None:
        """Remove all memory for an agent."""
        path = self.get_memory_path(agent_id)
        if path.exists():
            path.unlink()
            logger.info("memory cleared for %s", agent_id)

    def list_agents_with_memory(self) -> list[str]:
        """List all agent IDs that have memory files."""
        return [
            p.stem for p in self.memory_dir.glob("*.md")
            if p.stem != "README"
        ]
