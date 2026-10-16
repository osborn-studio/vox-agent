"""Tests for engine.hooks — Pre/PostToolUse interception chain."""

import pytest
from engine.hooks import HookChain, HookResult
from engine.permissions import ToolCall


@pytest.fixture
def chain():
    return HookChain()


class TestPreHooks:
    @pytest.mark.asyncio
    async def test_no_hooks_passthrough(self, chain):
        tc = ToolCall(tool="read", input={"file_path": "/tmp/test"})
        result = await chain.run_pre("agent-1", tc)
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_blocking_hook(self, chain):
        async def block_writes(agent_id, tool_call):
            if tool_call.tool == "write":
                return HookResult(blocked=True, reason="writes not allowed")
            return HookResult()

        chain.register_pre(block_writes)
        tc = ToolCall(tool="write", input={"file_path": "/tmp/x"})
        result = await chain.run_pre("agent-1", tc)
        assert result.blocked
        assert "writes not allowed" in result.block_reason

    @pytest.mark.asyncio
    async def test_non_blocking_hook(self, chain):
        async def log_hook(agent_id, tool_call):
            return HookResult()  # pass through

        chain.register_pre(log_hook)
        tc = ToolCall(tool="read")
        result = await chain.run_pre("agent-1", tc)
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_modifying_hook(self, chain):
        async def add_context(agent_id, tool_call):
            modified = ToolCall(
                tool=tool_call.tool,
                input={**tool_call.input, "context": "added"},
            )
            return HookResult(modified_call=modified)

        chain.register_pre(add_context)
        tc = ToolCall(tool="bash", input={"command": "ls"})
        result = await chain.run_pre("agent-1", tc)
        assert not result.blocked
        assert result.input.get("context") == "added"

    @pytest.mark.asyncio
    async def test_multiple_hooks_first_blocks(self, chain):
        calls = []

        async def hook_a(agent_id, tc):
            calls.append("a")
            return HookResult(blocked=True, reason="a blocked")

        async def hook_b(agent_id, tc):
            calls.append("b")
            return HookResult()

        chain.register_pre(hook_a)
        chain.register_pre(hook_b)
        tc = ToolCall(tool="write")
        result = await chain.run_pre("agent-1", tc)
        assert result.blocked
        assert calls == ["a"]  # b should not be called

    @pytest.mark.asyncio
    async def test_hook_error_continues(self, chain):
        async def bad_hook(agent_id, tc):
            raise ValueError("boom")

        async def good_hook(agent_id, tc):
            return HookResult()

        chain.register_pre(bad_hook)
        chain.register_pre(good_hook)
        tc = ToolCall(tool="read")
        result = await chain.run_pre("agent-1", tc)
        assert not result.blocked  # should continue despite error


class TestPostHooks:
    @pytest.mark.asyncio
    async def test_passthrough(self, chain):
        async def noop(agent_id, tc, result):
            return result

        chain.register_post(noop)
        tc = ToolCall(tool="read")
        result = await chain.run_post("agent-1", tc, "original output")
        assert result == "original output"

    @pytest.mark.asyncio
    async def test_transform_result(self, chain):
        async def uppercase(agent_id, tc, result):
            return result.upper() if isinstance(result, str) else result

        chain.register_post(uppercase)
        tc = ToolCall(tool="read")
        result = await chain.run_post("agent-1", tc, "hello")
        assert result == "HELLO"

    @pytest.mark.asyncio
    async def test_chained_transforms(self, chain):
        async def add_prefix(agent_id, tc, result):
            return f"[{tc.tool}] {result}"

        async def add_timestamp(agent_id, tc, result):
            return f"{result} @now"

        chain.register_post(add_prefix)
        chain.register_post(add_timestamp)
        tc = ToolCall(tool="bash")
        result = await chain.run_post("agent-1", tc, "output")
        assert result == "[bash] output @now"

    @pytest.mark.asyncio
    async def test_post_hook_error_continues(self, chain):
        async def bad(agent_id, tc, result):
            raise RuntimeError("post hook crash")

        chain.register_post(bad)
        tc = ToolCall(tool="read")
        result = await chain.run_post("agent-1", tc, "data")
        assert result == "data"  # original returned despite error
