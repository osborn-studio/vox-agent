"""Tests for engine.context — execution context assembly."""

import pytest

from engine.context import ContextBuilder
from engine.agent_memory import AgentMemory
from engine.skills import SkillLoader
from engine.agent_config import AgentConfigScanner
from engine.store import StoreManager
from engine.models import Task, TaskStatus, TaskMessage


@pytest.fixture
def components(tmp_path):
    memory = AgentMemory(tmp_path / "memory")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skills = SkillLoader(skills_dir)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    scanner = AgentConfigScanner(agents_dir)
    store = StoreManager(tmp_path / "db")
    store.connect()
    builder = ContextBuilder(memory, skills, scanner, store)
    yield builder, memory, skills_dir, agents_dir, store
    store.close()


class TestContextBuilder:
    def test_basic_context(self, components):
        builder, _, _, _, _ = components
        ctx = builder.build("claude-code", "write tests")
        assert "claude-code" in ctx.system_prompt
        assert ctx.estimated_tokens > 0

    def test_with_memory(self, components):
        builder, memory, _, _, _ = components
        memory.add("claude-code", "User prefers pytest -v")
        ctx = builder.build("claude-code", "run tests")
        assert "pytest -v" in ctx.memory
        assert "pytest" in ctx.to_system_message()

    def test_with_skills(self, components):
        builder, _, skills_dir, _, _ = components
        (skills_dir / "test-skill.md").write_text(
            "---\nname: test-skill\n---\nYou test things."
        )
        ctx = builder.build("claude-code", "test", skill_names=["test-skill"])
        assert len(ctx.skills) == 1
        assert "test things" in ctx.to_system_message()

    def test_with_agent_config(self, components):
        builder, _, _, agents_dir, _ = components
        agent_dir = agents_dir / "claude-code"
        agent_dir.mkdir()
        (agent_dir / "identity.md").write_text(
            "---\nid: claude-code\nname: Claude Code\nstatus: active\nskills: [code-review]\n---\n"
        )
        ctx = builder.build("claude-code", "review code")
        assert ctx.agent_config is not None
        assert ctx.agent_config.name == "Claude Code"

    def test_with_file_context(self, components, tmp_path):
        builder, _, _, _, _ = components
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): return 'world'")
        ctx = builder.build("claude", "review", file_paths=[str(test_file)])
        assert len(ctx.file_context) == 1
        assert "hello" in ctx.file_context[0]

    def test_with_history(self, components):
        builder, _, _, _, store = components
        t = Task(agent_id="claude", title="prev task")
        store.insert_task(t)
        store.update_task_status(t.id, TaskStatus.COMPLETED)
        store.insert_task_message(TaskMessage(
            task_id=t.id, seq=1,
            author_type="agent", author_id="claude",
            type="text", content="previous result",
        ))
        ctx = builder.build("claude", "new task")
        assert len(ctx.recent_messages) == 1

    def test_no_history_when_disabled(self, components):
        builder, _, _, _, _ = components
        ctx = builder.build("claude", "test", include_history=False)
        assert ctx.recent_messages == []

    def test_system_message_assembly(self, components):
        builder, memory, skills_dir, _, _ = components
        memory.add("claude", "important fact")
        (skills_dir / "s1.md").write_text("---\nname: s1\n---\nSkill content")

        ctx = builder.build("claude", "task", skill_names=["s1"])
        msg = ctx.to_system_message()

        assert "claude" in msg
        assert "important fact" in msg
        assert "Skill content" in msg

    def test_missing_file_handled(self, components):
        builder, _, _, _, _ = components
        ctx = builder.build("claude", "test", file_paths=["/nonexistent/file.py"])
        assert ctx.file_context == []

    def test_token_estimation(self, components):
        builder, _, _, _, _ = components
        ctx = builder.build("claude", "a" * 4000)
        assert ctx.estimated_tokens >= 1000
