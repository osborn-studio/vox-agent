"""Context Builder — assemble full execution context for agent tasks.

Combines:
- System prompt
- Agent memory (cross-session)
- Loaded skills
- Recent conversation history
- Task-specific context (files, references)
- User preferences
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .agent_memory import AgentMemory
from .skills import SkillLoader, Skill
from .agent_config import AgentConfigScanner, AgentConfig
from .store import StoreManager

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 50_000  # rough limit to stay within LLM context


@dataclass
class ExecutionContext:
    system_prompt: str
    agent_config: AgentConfig | None
    memory: str
    skills: list[Skill]
    recent_messages: list[dict]
    file_context: list[str]
    estimated_tokens: int

    def to_system_message(self) -> str:
        """Build the complete system prompt for the LLM."""
        parts = [self.system_prompt]

        if self.memory:
            parts.append(f"\n## Agent Memory\n{self.memory}")

        for skill in self.skills:
            parts.append(f"\n## Skill: {skill.name}\n{skill.prompt}")

        if self.file_context:
            parts.append("\n## File Context")
            for f in self.file_context:
                parts.append(f)

        return "\n".join(parts)


class ContextBuilder:
    """Build execution context for agent tasks."""

    def __init__(
        self,
        memory: AgentMemory,
        skills: SkillLoader,
        scanner: AgentConfigScanner,
        store: StoreManager,
    ) -> None:
        self.memory = memory
        self.skills = skills
        self.scanner = scanner
        self.store = store

    def build(
        self,
        agent_id: str,
        prompt: str,
        *,
        skill_names: list[str] | None = None,
        file_paths: list[str] | None = None,
        include_history: bool = True,
        max_history: int = 10,
    ) -> ExecutionContext:
        """Build the full execution context for a task."""

        # 1. Agent config
        agent_config = self.scanner.get_agent(agent_id)

        # 2. System prompt
        system_prompt = self._build_system_prompt(agent_id, agent_config)

        # 3. Memory
        memory_text = self.memory.get_context(agent_id, max_entries=20)

        # 4. Skills
        loaded_skills = self._load_skills(agent_id, agent_config, skill_names)

        # 5. Recent conversation history
        recent = []
        if include_history:
            recent = self._get_recent_messages(agent_id, max_history)

        # 6. File context
        file_ctx = []
        if file_paths:
            file_ctx = self._read_files(file_paths)

        # 7. Estimate tokens
        total_text = system_prompt + memory_text + prompt
        for s in loaded_skills:
            total_text += s.prompt
        for m in recent:
            total_text += m.get("content", "")
        for f in file_ctx:
            total_text += f
        estimated_tokens = len(total_text) // 4  # rough estimate

        return ExecutionContext(
            system_prompt=system_prompt,
            agent_config=agent_config,
            memory=memory_text,
            skills=loaded_skills,
            recent_messages=recent,
            file_context=file_ctx,
            estimated_tokens=estimated_tokens,
        )

    def _build_system_prompt(self, agent_id: str, config: AgentConfig | None) -> str:
        parts = [f"You are {agent_id}, a Vox Agents task executor."]

        if config:
            parts.append(f"Name: {config.name}")
            if config.provider:
                parts.append(f"Provider: {config.provider}")

        parts.append(
            "\nYou have access to tools for reading/writing files, "
            "running shell commands, and searching the web. "
            "Use them as needed to complete the task."
        )
        return "\n".join(parts)

    def _load_skills(
        self,
        agent_id: str,
        config: AgentConfig | None,
        explicit_skills: list[str] | None,
    ) -> list[Skill]:
        names = set()

        # From explicit request
        if explicit_skills:
            names.update(explicit_skills)

        # From agent config
        if config and config.skills:
            names.update(config.skills)

        loaded = []
        for name in names:
            s = self.skills.load_by_name(name)
            if s:
                loaded.append(s)

        return loaded

    def _get_recent_messages(self, agent_id: str, limit: int) -> list[dict]:
        """Get recent task messages for this agent."""
        tasks = self.store.list_tasks(agent_id=agent_id)
        if not tasks:
            return []

        # Get messages from the most recent completed task
        recent_completed = [t for t in tasks if t["status"] == "completed"]
        if not recent_completed:
            return []

        messages = self.store.list_task_messages(recent_completed[0]["id"])
        return [
            {"role": m["author_type"], "content": m.get("content", "")}
            for m in messages[-limit:]
            if m.get("content")
        ]

    def _read_files(self, paths: list[str], max_per_file: int = 5000) -> list[str]:
        """Read file contents for context injection."""
        from pathlib import Path

        results = []
        for p in paths[:10]:  # limit to 10 files
            try:
                path = Path(p).expanduser()
                if path.exists() and path.is_file():
                    content = path.read_text(errors="replace")
                    if len(content) > max_per_file:
                        content = content[:max_per_file] + f"\n... (truncated, {len(content)} chars total)"
                    results.append(f"### {p}\n```\n{content}\n```")
            except Exception:
                logger.warning("could not read %s", p)
        return results
