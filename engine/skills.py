"""Skill Loader — Markdown + YAML frontmatter (compatible with OpenHarness/Anthropic)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_DIR = resolve_vox_agent_data_dir() / "skills"


@dataclass
class Skill:
    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    permissions: dict = field(default_factory=dict)
    prompt: str = ""


class SkillLoader:
    """Load skill files from disk.

    Skill format: Markdown file with optional YAML frontmatter.
    Compatible with Anthropic CLAUDE.md skill format and OpenHarness plugin.json.
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self.skills_dir = skills_dir or (resolve_vox_agent_data_dir() / "skills")

    def load_all(self) -> list[Skill]:
        if not self.skills_dir.exists():
            return []
        skills = []
        for path in sorted(self.skills_dir.glob("*.md")):
            try:
                skills.append(self._parse(path))
            except Exception:
                logger.exception("failed to parse skill %s", path)
        logger.info("loaded %d skills from %s", len(skills), self.skills_dir)
        return skills

    def load_by_name(self, name: str) -> Skill | None:
        path = self.skills_dir / f"{name}.md"
        if not path.exists():
            return None
        return self._parse(path)

    def _parse(self, path: Path) -> Skill:
        content = path.read_text()
        meta: dict = {}
        prompt = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    import yaml
                    meta = yaml.safe_load(parts[1]) or {}
                except ImportError:
                    meta = self._parse_simple_yaml(parts[1])
                except Exception:
                    meta = {}
                prompt = parts[2].strip()

        return Skill(
            name=meta.get("name", path.stem),
            description=meta.get("description", ""),
            tools=meta.get("tools", []),
            permissions=meta.get("permissions", {}),
            prompt=prompt,
        )

    @staticmethod
    def _parse_simple_yaml(text: str) -> dict:
        """Minimal key:value parser when PyYAML is unavailable."""
        result: dict = {}
        current_key = None
        current_indent = 0

        for line in text.strip().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            indent = len(line) - len(line.lstrip())

            if ":" in stripped and indent == 0:
                k, v = stripped.split(":", 1)
                k, v = k.strip(), v.strip()
                if v.startswith("[") and v.endswith("]"):
                    v = [x.strip().strip("'\"") for x in v[1:-1].split(",")]
                    result[k] = v
                elif v:
                    result[k] = v
                else:
                    # nested block starts
                    current_key = k
                    result[k] = {}
                    current_indent = indent
            elif current_key and indent > current_indent and ":" in stripped:
                k2, v2 = stripped.split(":", 1)
                k2, v2 = k2.strip(), v2.strip()
                if isinstance(result[current_key], dict):
                    result[current_key][k2] = v2
            else:
                current_key = None
        return result
