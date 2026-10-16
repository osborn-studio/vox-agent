"""Agent Profiles — configurable behavior presets.

Each agent can have multiple profiles that change how it executes tasks:
- "coding": uses code-review + test-runner skills, auto permission mode
- "review": uses code-review skill, plan permission mode (read-only)
- "ops": uses deploy-staging skill, default permission mode (confirm writes)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir


logger = logging.getLogger(__name__)

DEFAULT_PROFILES_DIR = resolve_vox_agent_data_dir() / "profiles"


@dataclass
class AgentProfile:
    id: str
    name: str
    description: str = ""
    permission_mode: str = "default"  # "default" | "auto" | "plan"
    skills: list[str] = field(default_factory=list)
    system_prompt_suffix: str = ""
    max_iterations: int = 20
    timeout_seconds: int = 300
    provider_override: str = ""  # "" = use agent default


BUILTIN_PROFILES = [
    AgentProfile(
        id="coding",
        name="编码模式",
        description="Full coding agent with file editing and testing",
        permission_mode="auto",
        skills=["code-review", "test-runner"],
        max_iterations=30,
        timeout_seconds=600,
    ),
    AgentProfile(
        id="review",
        name="审查模式",
        description="Read-only code review, no modifications allowed",
        permission_mode="plan",
        skills=["code-review", "explain-code"],
        max_iterations=10,
        timeout_seconds=120,
    ),
    AgentProfile(
        id="ops",
        name="运维模式",
        description="Operations with confirmation for all writes",
        permission_mode="default",
        skills=["deploy-staging", "git-helper"],
        max_iterations=15,
        timeout_seconds=300,
    ),
    AgentProfile(
        id="research",
        name="调研模式",
        description="Research and search only, no code changes",
        permission_mode="plan",
        skills=["search-codebase", "explain-code"],
        max_iterations=10,
        timeout_seconds=120,
    ),
    AgentProfile(
        id="security",
        name="安全审计",
        description="Security-focused code audit",
        permission_mode="plan",
        skills=["security-audit"],
        max_iterations=15,
        timeout_seconds=180,
    ),
]


class ProfileManager:
    """Manage agent behavior profiles."""

    def __init__(self, profiles_dir: Path | None = None) -> None:
        self.profiles_dir = profiles_dir or (resolve_vox_agent_data_dir() / "profiles")
        self._profiles: dict[str, AgentProfile] = {}
        self._load()

    def _load(self) -> None:
        for p in BUILTIN_PROFILES:
            self._profiles[p.id] = p

        if self.profiles_dir.exists():
            for path in self.profiles_dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text())
                    p = AgentProfile(**data)
                    self._profiles[p.id] = p
                except Exception:
                    logger.exception("failed to load profile %s", path)

    def list_all(self) -> list[AgentProfile]:
        return list(self._profiles.values())

    def get(self, profile_id: str) -> AgentProfile | None:
        return self._profiles.get(profile_id)

    def save(self, profile: AgentProfile) -> None:
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        path = self.profiles_dir / f"{profile.id}.json"
        path.write_text(json.dumps(asdict(profile), indent=2, ensure_ascii=False))
        self._profiles[profile.id] = profile

    def delete(self, profile_id: str) -> bool:
        if profile_id in {p.id for p in BUILTIN_PROFILES}:
            return False
        path = self.profiles_dir / f"{profile_id}.json"
        if path.exists():
            path.unlink()
        return self._profiles.pop(profile_id, None) is not None

    def apply(self, profile_id: str) -> dict | None:
        """Get configuration dict to apply to an agent task execution."""
        p = self.get(profile_id)
        if not p:
            return None
        return {
            "permission_mode": p.permission_mode,
            "skills": p.skills,
            "max_iterations": p.max_iterations,
            "timeout_seconds": p.timeout_seconds,
            "system_prompt_suffix": p.system_prompt_suffix,
            "provider_override": p.provider_override,
        }
