"""End-to-end ownership propagation from engine execution to permission checks."""

from __future__ import annotations

import pytest

from engine.engine import AgentEngine
from engine.hooks import HookChain
from engine.permissions import ToolCall
from engine.skills import SkillLoader
from engine.tools import ToolDefinition, ToolRegistry, ToolResult


@pytest.mark.asyncio
async def test_direct_engine_forwards_requester_to_permission_manager(monkeypatch):
    captured = {}

    class PermissionSpy:
        async def check(self, tool_call: ToolCall, **kwargs):
            captured["tool_call"] = tool_call
            captured.update(kwargs)
            return False

    registry = ToolRegistry()

    async def write_handler(_input):
        return ToolResult(output="unused")

    registry.register(ToolDefinition(
        name="write",
        description="Write a file",
        parameters={"type": "object"},
        handler=write_handler,
    ))
    engine = AgentEngine(
        registry,
        PermissionSpy(),
        HookChain(),
        SkillLoader(),
    )
    calls = 0

    async def fake_call_llm(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "text": "",
                "tool_calls": [{
                    "id": "tool-1",
                    "name": "write",
                    "input": {"file_path": "/tmp/report.md"},
                }],
            }
        return {"text": "done", "tool_calls": []}

    monkeypatch.setattr(engine, "_call_llm", fake_call_llm)

    events = [
        event
        async for event in engine.run(
            "codex",
            "write the report",
            task_id="task-123",
            provider="fake-direct",
            requester_user_id="user-123",
            workspace_id="workspace-456",
        )
    ]

    assert captured["tool_call"].tool == "write"
    assert captured["task_id"] == "task-123"
    assert captured["agent_id"] == "codex"
    assert captured["requester_user_id"] == "user-123"
    assert captured["workspace_id"] == "workspace-456"
    assert any(event.data == "permission denied: write" for event in events)
