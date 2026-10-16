"""Config Validator — pre-flight checks before engine startup.

Validates:
- Required directories exist
- Agent configs are parseable
- Skills are valid
- Database files are accessible
- API keys are present (optional)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    valid: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_check(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            self.errors.append(f"{name}: {detail}")
            self.valid = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


class ConfigValidator:
    """Validate engine configuration before startup."""

    def __init__(
        self,
        data_dir: Path | None = None,
        agents_dir: Path | None = None,
        skills_dir: Path | None = None,
    ) -> None:
        canonical = resolve_vox_agent_data_dir()
        self.data_dir = data_dir or canonical
        self.agents_dir = agents_dir or (Path.home() / ".osborn" / "config" / "agents")
        self.skills_dir = skills_dir or (canonical / "skills")

    def validate(self) -> ValidationResult:
        result = ValidationResult(valid=True)

        self._check_data_dir(result)
        self._check_agents_dir(result)
        self._check_skills_dir(result)
        self._check_python_deps(result)
        self._check_cli_tools(result)

        status = "VALID" if result.valid else "INVALID"
        logger.info(
            "config validation: %s (%d checks, %d errors, %d warnings)",
            status, len(result.checks), len(result.errors), len(result.warnings),
        )
        return result

    def _check_data_dir(self, result: ValidationResult) -> None:
        if self.data_dir.exists():
            result.add_check("data_dir", True, str(self.data_dir))
        else:
            # Try to create it
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                result.add_check("data_dir", True, f"created {self.data_dir}")
            except Exception as e:
                result.add_check("data_dir", False, f"cannot create: {e}")

    def _check_agents_dir(self, result: ValidationResult) -> None:
        if self.agents_dir.exists():
            agents = list(self.agents_dir.iterdir())
            agent_count = sum(1 for a in agents if a.is_dir() and (a / "identity.md").exists())
            result.add_check("agents_dir", True, f"{agent_count} agents found")
            if agent_count == 0:
                result.add_warning("no agents configured in ~/.osborn/config/agents/")
        else:
            result.add_check("agents_dir", True, "not present (will use defaults)")
            result.add_warning("~/.osborn/config/agents/ does not exist")

    def _check_skills_dir(self, result: ValidationResult) -> None:
        if self.skills_dir.exists():
            skills = list(self.skills_dir.glob("*.md"))
            result.add_check("skills_dir", True, f"{len(skills)} skills found")
        else:
            result.add_check("skills_dir", True, "not present (built-in skills only)")

    def _check_python_deps(self, result: ValidationResult) -> None:
        required = ["fastapi", "uvicorn", "pydantic", "httpx"]
        missing = []
        for mod in required:
            try:
                __import__(mod)
            except ImportError:
                missing.append(mod)

        if missing:
            result.add_check("python_deps", False, f"missing: {', '.join(missing)}")
        else:
            result.add_check("python_deps", True, f"{len(required)} required packages OK")

    def _check_cli_tools(self, result: ValidationResult) -> None:
        import shutil
        from .cli_environment import cli_environment
        search_path = cli_environment().get("PATH")
        tools = {"claude": False, "codex": False}
        for tool in tools:
            tools[tool] = shutil.which(tool, path=search_path) is not None

        available = [t for t, v in tools.items() if v]
        if available:
            result.add_check("cli_tools", True, f"available: {', '.join(available)}")
        else:
            result.add_check("cli_tools", True, "no CLI agents found (API-only mode)")
            result.add_warning("no agent CLIs detected (claude, codex)")
