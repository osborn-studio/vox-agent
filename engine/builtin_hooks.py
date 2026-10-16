"""Built-in hooks — security checks and automatic archiving."""

from __future__ import annotations

import logging
from typing import Any

from .hooks import HookChain, HookResult
from .permissions import ToolCall
from .store import StoreManager
from .models import ActivityEntry
from .agent_memory import AgentMemory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PreToolUse: Security Check
# ---------------------------------------------------------------------------

# Patterns that should never appear in bash commands
DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "> /dev/sda",
    "mkfs.",
    ":(){ :|:& };:",  # fork bomb
    "dd if=/dev/zero of=/dev/",
]


async def security_check_hook(agent_id: str, tool_call: ToolCall) -> HookResult:
    """Block obviously dangerous operations regardless of permission mode."""
    if tool_call.tool != "bash":
        return HookResult()

    cmd = tool_call.input.get("command", "")
    for pattern in DANGEROUS_PATTERNS:
        if pattern in cmd:
            logger.warning(
                "BLOCKED dangerous command from %s: %s",
                agent_id, cmd[:100],
            )
            return HookResult(blocked=True, reason=f"dangerous pattern: {pattern}")

    return HookResult()


# ---------------------------------------------------------------------------
# PostToolUse: Activity Archive
# ---------------------------------------------------------------------------

def make_archive_hook(store: StoreManager) -> Any:
    """Create a post-hook that logs all tool executions to activity.db."""

    async def archive_hook(
        agent_id: str, tool_call: ToolCall, result: Any
    ) -> Any:
        output = ""
        success = True
        if hasattr(result, "output"):
            output = result.output[:500]  # truncate for storage
            success = result.success
        elif isinstance(result, str):
            output = result[:500]

        store.insert_activity(ActivityEntry(
            actor_type="agent",
            actor_id=agent_id,
            action=f"tool:{tool_call.tool}",
            target_type="tool",
            target_id=tool_call.tool,
            details={
                "input": tool_call.input,
                "output_preview": output[:200],
                "success": success,
            },
        ))
        return result

    return archive_hook


# ---------------------------------------------------------------------------
# PostToolUse: Auto-Memory
# ---------------------------------------------------------------------------

def make_memory_hook(memory: AgentMemory) -> Any:
    """Create a post-hook that saves significant tool results to agent memory.

    Only saves results from write/edit operations (modifications),
    not read-only operations.
    """

    WRITE_TOOLS = {"write", "edit", "bash"}

    async def memory_hook(
        agent_id: str, tool_call: ToolCall, result: Any
    ) -> Any:
        if tool_call.tool not in WRITE_TOOLS:
            return result

        success = getattr(result, "success", True)
        if not success:
            return result

        # Build a concise memory entry
        if tool_call.tool == "edit":
            path = tool_call.input.get("file_path", "")
            memory.add(
                agent_id,
                f"Edited {path}",
                source="agent",
            )
        elif tool_call.tool == "write":
            path = tool_call.input.get("file_path", "")
            memory.add(
                agent_id,
                f"Created/wrote {path}",
                source="agent",
            )

        return result

    return memory_hook


# ---------------------------------------------------------------------------
# Register all built-in hooks
# ---------------------------------------------------------------------------

def register_builtin_hooks(
    hook_chain: "HookChain",
    store: StoreManager,
    memory: AgentMemory,
) -> None:
    """Register all built-in pre/post hooks."""
    hook_chain.register_pre(security_check_hook)
    hook_chain.register_post(make_archive_hook(store))
    hook_chain.register_post(make_memory_hook(memory))
