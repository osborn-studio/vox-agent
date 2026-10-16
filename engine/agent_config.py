"""Agent Config — scan ~/.osborn/config/agents/ for agent definitions.

Mirrors the Swift WorkspaceScanner: reads identity.md files with YAML frontmatter.
This allows the Python engine to know which agents exist and their capabilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_AGENTS_DIR = Path.home() / ".osborn" / "config" / "agents"


@dataclass
class AgentConfig:
    id: str
    name: str
    emoji: str = "🤖"
    status: str = "active"
    parent_id: str | None = None
    external_system: str | None = None  # "openclaw"
    external_agent_ref: str | None = None
    provider: str = "claude"  # "claude" | "codex" | "opencode" | "custom"
    skills: list[str] = field(default_factory=list)
    max_concurrent: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentConfigScanner:
    """Scan ~/.osborn/config/agents/ directory for agent identity files.

    Each agent is a directory containing an identity.md file:
    ```
    ~/.osborn/config/agents/
    ├── claude-code/
    │   └── identity.md
    ├── openclaw/
    │   └── identity.md
    └── osborn/
        └── identity.md
    ```

    identity.md format (YAML frontmatter):
    ```
    ---
    id: claude-code
    name: Claude Code
    icon: 👨‍💻
    status: active
    parent: osborn
    provider: claude
    ---
    Claude Code is a coding agent...
    ```
    """

    def __init__(self, agents_dir: Path | None = None) -> None:
        self.agents_dir = agents_dir or DEFAULT_AGENTS_DIR

    def scan(self) -> list[AgentConfig]:
        """Scan all agent directories and return configs."""
        if not self.agents_dir.exists():
            logger.info("agents dir not found: %s", self.agents_dir)
            return []

        agents = []
        for agent_dir in sorted(self.agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            if agent_dir.name.startswith(".") or agent_dir.name == "archive":
                continue

            identity = agent_dir / "identity.md"
            if not identity.exists():
                continue

            try:
                config = self._parse_identity(agent_dir.name, identity)
                if config.status == "active":
                    agents.append(config)
            except Exception:
                logger.exception("failed to parse %s", identity)

        logger.info("scanned %d agents from %s", len(agents), self.agents_dir)
        return agents

    def get_agent(self, agent_id: str) -> AgentConfig | None:
        """Get a specific agent config."""
        agent_dir = self.agents_dir / agent_id
        identity = agent_dir / "identity.md"
        if not identity.exists():
            return None
        return self._parse_identity(agent_id, identity)

    def _parse_identity(self, dir_name: str, path: Path) -> AgentConfig:
        """Parse an identity.md file."""
        content = path.read_text()
        meta: dict = {}

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                meta = self._parse_frontmatter(parts[1])

        # Fallback to markdown key-value
        if not meta:
            meta = self._parse_markdown_kv(content)

        return AgentConfig(
            id=meta.get("id", dir_name),
            name=meta.get("name", dir_name),
            emoji=meta.get("icon", meta.get("emoji", "🤖")),
            status=meta.get("status", "active"),
            parent_id=meta.get("parent", meta.get("parent_id")),
            external_system=meta.get("external_system"),
            external_agent_ref=meta.get("external_agent_ref"),
            provider=meta.get("provider", "claude"),
            skills=[s.strip() for s in meta.get("skills", "").split(",") if s.strip()]
                if isinstance(meta.get("skills"), str)
                else meta.get("skills", []),
            max_concurrent=int(meta.get("max_concurrent", 1)),
        )

    @staticmethod
    def _parse_frontmatter(text: str) -> dict:
        """Parse YAML-ish frontmatter (without requiring PyYAML)."""
        result: dict = {}
        for line in text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                k, v = k.strip(), v.strip()
                if v.startswith("[") and v.endswith("]"):
                    v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                result[k] = v
        return result

    @staticmethod
    def _parse_markdown_kv(content: str) -> dict:
        """Fallback: parse '- **Key:** Value' format."""
        import re
        result = {}
        for match in re.finditer(r"-\s+\*\*(\w+):\*\*\s+(.*)", content):
            result[match.group(1).lower()] = match.group(2).strip()
        return result
