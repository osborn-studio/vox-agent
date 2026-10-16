"""Tests for engine.tools — ToolRegistry and built-in tools."""

import pytest
import tempfile
from pathlib import Path

from engine.tools import ToolRegistry
from engine.tools.file_tools import register_file_tools
from engine.tools.shell_tools import register_shell_tools


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_file_tools(r)
    register_shell_tools(r)
    return r


class TestToolRegistry:
    def test_builtin_count(self, registry):
        assert len(registry.list_tools()) == 6

    def test_builtin_names(self, registry):
        names = {t.name for t in registry.list_tools()}
        assert names == {"read", "write", "edit", "glob", "grep", "bash"}

    def test_get_existing(self, registry):
        assert registry.get("read") is not None

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_execute_unknown(self, registry):
        result = await registry.execute("nope", {})
        assert not result.success
        assert "unknown" in result.error

    def test_list_for_agent_filtered(self, registry):
        tools = registry.list_for_agent({"read", "grep"})
        assert len(tools) == 2


class TestReadTool:
    @pytest.mark.asyncio
    async def test_read_file(self, registry):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world\nsecond line\n")
            path = f.name
        result = await registry.execute("read", {"file_path": path})
        assert result.success
        assert "hello world" in result.output

    @pytest.mark.asyncio
    async def test_read_with_limit(self, registry):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            path = f.name
        result = await registry.execute("read", {"file_path": path, "limit": 1})
        assert "line1" in result.output
        assert "line2" not in result.output

    @pytest.mark.asyncio
    async def test_read_missing_file(self, registry):
        result = await registry.execute("read", {"file_path": "/nonexistent/file"})
        assert not result.success

    @pytest.mark.asyncio
    async def test_read_no_path(self, registry):
        result = await registry.execute("read", {})
        assert not result.success


class TestWriteTool:
    @pytest.mark.asyncio
    async def test_write_file(self, registry):
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/test.txt"
            result = await registry.execute("write", {"file_path": path, "content": "hello"})
            assert result.success
            assert Path(path).read_text() == "hello"

    @pytest.mark.asyncio
    async def test_write_creates_dirs(self, registry):
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/a/b/c/test.txt"
            result = await registry.execute("write", {"file_path": path, "content": "deep"})
            assert result.success
            assert Path(path).read_text() == "deep"


class TestEditTool:
    @pytest.mark.asyncio
    async def test_edit_replace(self, registry):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world")
            path = f.name
        result = await registry.execute("edit", {
            "file_path": path, "old_string": "world", "new_string": "vox",
        })
        assert result.success
        assert Path(path).read_text() == "hello vox"

    @pytest.mark.asyncio
    async def test_edit_not_found(self, registry):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello")
            path = f.name
        result = await registry.execute("edit", {
            "file_path": path, "old_string": "missing", "new_string": "x",
        })
        assert not result.success


class TestGrepTool:
    @pytest.mark.asyncio
    async def test_grep_match(self, registry):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line one\nfoo bar baz\nline three\n")
            path = f.name
        result = await registry.execute("grep", {"pattern": "foo", "path": path})
        assert result.success
        assert "foo bar baz" in result.output

    @pytest.mark.asyncio
    async def test_grep_no_match(self, registry):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("nothing here\n")
            path = f.name
        result = await registry.execute("grep", {"pattern": "zzz", "path": path})
        assert "no matches" in result.output


class TestBashTool:
    @pytest.mark.asyncio
    async def test_echo(self, registry):
        result = await registry.execute("bash", {"command": "echo hello"})
        assert result.success
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_exit_code(self, registry):
        result = await registry.execute("bash", {"command": "exit 1"})
        assert not result.success
        assert "exit code 1" in result.error

    @pytest.mark.asyncio
    async def test_timeout(self, registry):
        result = await registry.execute("bash", {"command": "sleep 10", "timeout": 1})
        assert not result.success
        assert "timed out" in result.error
