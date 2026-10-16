"""Tests for engine.agent_memory — per-agent persistent memory."""

import pytest
from engine.agent_memory import AgentMemory


@pytest.fixture
def mem(tmp_path):
    return AgentMemory(tmp_path / "memory")


class TestAgentMemory:
    def test_empty_load(self, mem):
        assert mem.load("nonexistent") == []

    def test_add_and_load(self, mem):
        mem.add("claude-code", "User prefers pytest -v")
        entries = mem.load("claude-code")
        assert len(entries) == 1
        assert "pytest -v" in entries[0].content

    def test_multiple_entries(self, mem):
        mem.add("claude-code", "First memory")
        mem.add("claude-code", "Second memory")
        entries = mem.load("claude-code")
        assert len(entries) == 2
        assert "First" in entries[0].content
        assert "Second" in entries[1].content

    def test_source_preserved(self, mem):
        mem.add("agent-1", "agent observation", source="agent")
        mem.add("agent-1", "user correction", source="user")
        entries = mem.load("agent-1")
        assert entries[0].source == "agent"
        assert entries[1].source == "user"

    def test_get_context_empty(self, mem):
        assert mem.get_context("no-agent") == ""

    def test_get_context_formatted(self, mem):
        mem.add("test", "fact one")
        mem.add("test", "fact two")
        ctx = mem.get_context("test")
        assert "Agent Memory" in ctx
        assert "fact one" in ctx
        assert "fact two" in ctx

    def test_get_context_max_entries(self, mem):
        for i in range(30):
            mem.add("test", f"memory-{i}")
        ctx = mem.get_context("test", max_entries=5)
        assert "memory-25" in ctx
        assert "memory-29" in ctx
        assert "memory-0" not in ctx

    def test_clear(self, mem):
        mem.add("test", "data")
        assert len(mem.load("test")) == 1
        mem.clear("test")
        assert len(mem.load("test")) == 0

    def test_clear_nonexistent(self, mem):
        mem.clear("nope")  # should not crash

    def test_list_agents(self, mem):
        mem.add("claude-code", "x")
        mem.add("codex", "y")
        agents = mem.list_agents_with_memory()
        assert set(agents) == {"claude-code", "codex"}

    def test_safe_filename(self, mem):
        mem.add("agent/sub", "test")
        assert (mem.memory_dir / "agent-sub.md").exists()

    def test_file_format(self, mem):
        mem.add("test-agent", "important fact", source="user")
        content = (mem.memory_dir / "test-agent.md").read_text()
        assert content.startswith("# Agent Memory: test-agent")
        assert "[user]" in content
        assert "important fact" in content

    def test_separate_agents(self, mem):
        mem.add("agent-a", "data for A")
        mem.add("agent-b", "data for B")
        a_entries = mem.load("agent-a")
        b_entries = mem.load("agent-b")
        assert len(a_entries) == 1
        assert len(b_entries) == 1
        assert "data for A" in a_entries[0].content
        assert "data for B" in b_entries[0].content
