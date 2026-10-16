"""Task Templates — reusable task definitions for common workflows.

Templates are stored as JSON files in the Vox Agent data directory.
Users can create tasks from templates via voice:
  "用 code-review 模板检查 auth 模块"
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATES_DIR = resolve_vox_agent_data_dir() / "templates"


@dataclass
class TaskTemplate:
    id: str
    name: str
    description: str = ""
    agent_id: str = "claude-code"
    prompt_template: str = ""  # use {input} as placeholder
    priority: int = 1
    skills: list[str] = field(default_factory=list)
    provider: str = "claude"
    tags: list[str] = field(default_factory=list)


# Built-in templates
BUILTIN_TEMPLATES = [
    TaskTemplate(
        id="code-review",
        name="代码审查",
        description="Review code changes for quality, security, and bugs",
        agent_id="claude-code",
        prompt_template="Review the following code or file for quality, security issues, and bugs:\n\n{input}",
        priority=1,
        skills=["code-review"],
        tags=["review", "quality"],
    ),
    TaskTemplate(
        id="run-tests",
        name="运行测试",
        description="Run the test suite and report results",
        agent_id="claude-code",
        prompt_template="Run the tests for: {input}\n\nReport pass/fail counts, any failures with details.",
        priority=1,
        skills=["test-runner"],
        tags=["test", "ci"],
    ),
    TaskTemplate(
        id="fix-bug",
        name="修复 Bug",
        description="Diagnose and fix a bug",
        agent_id="claude-code",
        prompt_template="Diagnose and fix this bug:\n\n{input}\n\nShow the root cause and the fix.",
        priority=2,
        skills=[],
        tags=["bug", "fix"],
    ),
    TaskTemplate(
        id="write-docs",
        name="写文档",
        description="Generate or update documentation",
        agent_id="claude-code",
        prompt_template="Write or update documentation for:\n\n{input}",
        priority=0,
        skills=["doc-writer"],
        tags=["docs"],
    ),
    TaskTemplate(
        id="refactor",
        name="重构代码",
        description="Refactor code for better structure and clarity",
        agent_id="claude-code",
        prompt_template="Refactor the following for better structure, readability, and maintainability:\n\n{input}",
        priority=1,
        skills=[],
        tags=["refactor", "quality"],
    ),
    TaskTemplate(
        id="search-code",
        name="搜索代码",
        description="Search the codebase for patterns, functions, or files",
        agent_id="claude-code",
        prompt_template="Search the codebase for:\n\n{input}\n\nReport file paths, line numbers, and context.",
        priority=0,
        skills=["search-codebase"],
        provider="claude",
        tags=["search"],
    ),
]


class TemplateManager:
    """Manage task templates — built-in + user-defined."""

    def __init__(self, templates_dir: Path | None = None) -> None:
        self.templates_dir = templates_dir or (resolve_vox_agent_data_dir() / "templates")
        self._templates: dict[str, TaskTemplate] = {}
        self._load()

    def _load(self) -> None:
        # Load built-ins
        for t in BUILTIN_TEMPLATES:
            self._templates[t.id] = t

        # Load user templates
        if self.templates_dir.exists():
            for path in self.templates_dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text())
                    t = TaskTemplate(**data)
                    self._templates[t.id] = t
                except Exception:
                    logger.exception("failed to load template %s", path)

        logger.info("loaded %d templates (%d built-in, %d user)",
                     len(self._templates), len(BUILTIN_TEMPLATES),
                     len(self._templates) - len(BUILTIN_TEMPLATES))

    def list_all(self) -> list[TaskTemplate]:
        return list(self._templates.values())

    def get(self, template_id: str) -> TaskTemplate | None:
        return self._templates.get(template_id)

    def render(self, template_id: str, input_text: str) -> dict | None:
        """Render a template with input text. Returns task creation params."""
        t = self.get(template_id)
        if not t:
            return None
        return {
            "agent_id": t.agent_id,
            "title": f"{t.name}: {input_text[:40]}",
            "prompt": t.prompt_template.replace("{input}", input_text),
            "priority": t.priority,
            "provider": t.provider,
            "skills": t.skills,
        }

    def save_user_template(self, template: TaskTemplate) -> None:
        """Save a user-defined template to disk."""
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        path = self.templates_dir / f"{template.id}.json"
        path.write_text(json.dumps(asdict(template), indent=2, ensure_ascii=False))
        self._templates[template.id] = template
        logger.info("saved template: %s", template.id)

    def delete_template(self, template_id: str) -> bool:
        """Delete a user template (built-ins cannot be deleted)."""
        if template_id in {t.id for t in BUILTIN_TEMPLATES}:
            return False
        path = self.templates_dir / f"{template_id}.json"
        if path.exists():
            path.unlink()
        self._templates.pop(template_id, None)
        return True

    def match_by_keywords(self, text: str) -> TaskTemplate | None:
        """Find a template matching keywords in the input text."""
        text_lower = text.lower()
        for t in self._templates.values():
            for tag in t.tags:
                if tag in text_lower:
                    return t
            if t.name.lower() in text_lower:
                return t
        return None
