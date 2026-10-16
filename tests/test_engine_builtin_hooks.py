"""Tests for engine.builtin_hooks — security checks and archiving."""

import pytest

from engine.builtin_hooks import (
    security_check_hook,
    make_archive_hook,
    make_memory_hook,
    register_builtin_hooks,
)
from engine.hooks import HookChain
from engine.permissions import ToolCall
from engine.store import StoreManager
from engine.agent_memory import AgentMemory
from engine.tools import ToolResult


@pytest.fixture
def store(tmp_path):
    s = StoreManager(tmp_path / "db")
    s.connect()
    yield s
    s.close()


@pytest.fixture
def memory(tmp_path):
    return AgentMemory(tmp_path / "memory")


class TestSecurityCheck:
    @pytest.mark.asyncio
    async def test_blocks_rm_rf_root(self):
        tc = ToolCall(tool="bash", input={"command": "rm -rf /"})
        result = await security_check_hook("agent-1", tc)
        assert result.blocked

    @pytest.mark.asyncio
    async def test_blocks_rm_rf_home(self):
        tc = ToolCall(tool="bash", input={"command": "rm -rf ~"})
        result = await security_check_hook("agent-1", tc)
        assert result.blocked

    @pytest.mark.asyncio
    async def test_blocks_fork_bomb(self):
        tc = ToolCall(tool="bash", input={"command": ":(){ :|:& };:"})
        result = await security_check_hook("agent-1", tc)
        assert result.blocked

    @pytest.mark.asyncio
    async def test_allows_safe_commands(self):
        tc = ToolCall(tool="bash", input={"command": "ls -la"})
        result = await security_check_hook("agent-1", tc)
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_ignores_non_bash(self):
        tc = ToolCall(tool="read", input={"file_path": "/etc/passwd"})
        result = await security_check_hook("agent-1", tc)
        assert not result.blocked


class TestArchiveHook:
    @pytest.mark.asyncio
    async def test_logs_tool_execution(self, store):
        hook = make_archive_hook(store)
        tc = ToolCall(tool="bash", input={"command": "echo hello"})
        result = ToolResult(output="hello\n", success=True)

        await hook("claude-code", tc, result)

        rows = store._activity_db.execute(
            "SELECT * FROM activity_log WHERE action = 'tool:bash'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["actor_id"] == "claude-code"

    @pytest.mark.asyncio
    async def test_returns_result_unchanged(self, store):
        hook = make_archive_hook(store)
        tc = ToolCall(tool="read", input={})
        original = ToolResult(output="data")
        returned = await hook("agent", tc, original)
        assert returned is original


class TestMemoryHook:
    @pytest.mark.asyncio
    async def test_saves_edit_to_memory(self, memory):
        hook = make_memory_hook(memory)
        tc = ToolCall(tool="edit", input={"file_path": "/tmp/auth.py"})
        result = ToolResult(output="edited", success=True)

        await hook("claude-code", tc, result)
        entries = memory.load("claude-code")
        assert len(entries) == 1
        assert "auth.py" in entries[0].content

    @pytest.mark.asyncio
    async def test_saves_write_to_memory(self, memory):
        hook = make_memory_hook(memory)
        tc = ToolCall(tool="write", input={"file_path": "/tmp/new.py"})
        result = ToolResult(output="wrote", success=True)

        await hook("claude-code", tc, result)
        entries = memory.load("claude-code")
        assert any("new.py" in e.content for e in entries)

    @pytest.mark.asyncio
    async def test_skips_read_operations(self, memory):
        hook = make_memory_hook(memory)
        tc = ToolCall(tool="read", input={"file_path": "/tmp/file"})
        result = ToolResult(output="content")
        await hook("agent", tc, result)
        assert memory.load("agent") == []

    @pytest.mark.asyncio
    async def test_skips_failed_operations(self, memory):
        hook = make_memory_hook(memory)
        tc = ToolCall(tool="write", input={"file_path": "/nope"})
        result = ToolResult(error="permission denied", success=False)
        await hook("agent", tc, result)
        assert memory.load("agent") == []


class TestRegisterBuiltinHooks:
    def test_registers_all(self, store, memory):
        chain = HookChain()
        register_builtin_hooks(chain, store, memory)
        assert len(chain._pre) == 1   # security check
        assert len(chain._post) == 2  # archive + memory
