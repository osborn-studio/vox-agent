"""Agent Capabilities — define what each agent can and cannot do.

Maps agents to their allowed tools, skills, and operation types.
Used by the permission system to enforce capability boundaries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AgentCapability:
    agent_id: str
    allowed_tools: set[str] = field(default_factory=set)
    denied_tools: set[str] = field(default_factory=set)
    allowed_skills: set[str] = field(default_factory=set)
    max_file_size_bytes: int = 10_000_000  # 10MB
    can_execute_shell: bool = True
    can_write_files: bool = True
    can_access_network: bool = True
    max_concurrent: int = 1
    timeout_seconds: int = 7200


# Default capabilities per provider
DEFAULT_CAPABILITIES = {
    "claude": AgentCapability(
        agent_id="claude",
        allowed_tools={"read", "write", "edit", "glob", "grep", "bash", "web_search", "web_fetch"},
        can_execute_shell=True,
        can_write_files=True,
        can_access_network=True,
        max_concurrent=3,
    ),
    "codex": AgentCapability(
        agent_id="codex",
        allowed_tools={"read", "write", "edit", "glob", "grep", "bash"},
        can_execute_shell=True,
        can_write_files=True,
        can_access_network=False,
        max_concurrent=2,
    ),
    "review-only": AgentCapability(
        agent_id="review-only",
        allowed_tools={"read", "glob", "grep"},
        can_execute_shell=False,
        can_write_files=False,
        can_access_network=False,
        max_concurrent=5,
        timeout_seconds=300,
    ),
}


class CapabilityManager:
    """Manage agent capability definitions."""

    def __init__(self) -> None:
        self._capabilities: dict[str, AgentCapability] = dict(DEFAULT_CAPABILITIES)

    def get(self, agent_id: str) -> AgentCapability:
        """Get capabilities for an agent. Falls back to default claude profile."""
        return self._capabilities.get(agent_id, DEFAULT_CAPABILITIES["claude"])

    def set(self, agent_id: str, capability: AgentCapability) -> None:
        self._capabilities[agent_id] = capability

    def is_tool_allowed(self, agent_id: str, tool_name: str) -> bool:
        cap = self.get(agent_id)
        if tool_name in cap.denied_tools:
            return False
        if cap.allowed_tools and tool_name not in cap.allowed_tools:
            return False
        return True

    def can_shell(self, agent_id: str) -> bool:
        return self.get(agent_id).can_execute_shell

    def can_write(self, agent_id: str) -> bool:
        return self.get(agent_id).can_write_files

    def can_network(self, agent_id: str) -> bool:
        return self.get(agent_id).can_access_network

    def list_all(self) -> list[AgentCapability]:
        return list(self._capabilities.values())

    def summary(self, agent_id: str) -> dict:
        cap = self.get(agent_id)
        return {
            "agent_id": agent_id,
            "tools": sorted(cap.allowed_tools),
            "denied_tools": sorted(cap.denied_tools),
            "shell": cap.can_execute_shell,
            "write": cap.can_write_files,
            "network": cap.can_access_network,
            "max_concurrent": cap.max_concurrent,
            "timeout": cap.timeout_seconds,
        }
