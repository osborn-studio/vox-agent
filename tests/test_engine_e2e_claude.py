"""End-to-end test with actual claude CLI.

This test requires:
- claude CLI installed and authenticated
- Network access for API calls
- VOXAGENT_RUN_E2E=1 explicitly set

Run intentionally with: VOXAGENT_RUN_E2E=1 pytest -m e2e
"""

import asyncio
import os
import shutil
import pytest

from engine.engine import AgentEngine
from engine.tools import ToolRegistry
from engine.tools.file_tools import register_file_tools
from engine.tools.shell_tools import register_shell_tools
from engine.permissions import PermissionManager, PermissionConfig, PermissionMode
from engine.hooks import HookChain
from engine.skills import SkillLoader
from engine.models import EventType

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("VOXAGENT_RUN_E2E") != "1",
        reason="external Claude CLI tests require VOXAGENT_RUN_E2E=1",
    ),
]


def has_claude():
    return shutil.which("claude") is not None


@pytest.fixture
def engine():
    reg = ToolRegistry()
    register_file_tools(reg)
    register_shell_tools(reg)
    return AgentEngine(
        reg,
        PermissionManager(PermissionConfig(mode=PermissionMode.AUTO)),
        HookChain(),
        SkillLoader(),
    )


@pytest.mark.skipif(not has_claude(), reason="claude CLI not installed")
class TestClaudeCLI:
    @pytest.mark.asyncio
    async def test_simple_question(self, engine):
        """Ask claude a simple question, verify we get a result."""
        events = []
        async for event in engine.run(
            "claude-code",
            "What is 3+5? Reply with just the number.",
            provider="claude",
        ):
            events.append(event)
            if len(events) > 50:
                break

        # Should have at least a DONE event
        done_events = [e for e in events if e.type == EventType.DONE]
        assert len(done_events) >= 1

        # Check result contains "8"
        for e in done_events:
            if e.result and "result" in e.result:
                assert "8" in str(e.result["result"])
                break

    @pytest.mark.asyncio
    async def test_stop(self, engine):
        """Verify we can stop a running agent."""
        task = asyncio.create_task(self._collect_events(engine))
        await asyncio.sleep(1)
        await engine.stop("claude-code")
        # Should not hang
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()

    async def _collect_events(self, engine):
        async for event in engine.run(
            "claude-code",
            "Count slowly from 1 to 100, saying each number.",
            provider="claude",
        ):
            pass
