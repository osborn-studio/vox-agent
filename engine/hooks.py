"""Hook chain — Pre/PostToolUse lifecycle interception (inspired by OpenHarness)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from .permissions import ToolCall

logger = logging.getLogger(__name__)


@dataclass
class HookResult:
    blocked: bool = False
    modified_call: ToolCall | None = None
    reason: str = ""


PreHook = Callable[[str, ToolCall], Awaitable[HookResult]]
PostHook = Callable[[str, ToolCall, Any], Awaitable[Any]]


class HookChain:
    """Ordered list of pre- and post-tool-use interceptors.

    Pre hooks can block or modify a tool call.
    Post hooks can transform the result (e.g. archive it).
    """

    def __init__(self) -> None:
        self._pre: list[PreHook] = []
        self._post: list[PostHook] = []

    def register_pre(self, hook: PreHook) -> None:
        self._pre.append(hook)

    def register_post(self, hook: PostHook) -> None:
        self._post.append(hook)

    async def run_pre(self, agent_id: str, tool_call: ToolCall) -> ToolCall:
        for hook in self._pre:
            try:
                result = await hook(agent_id, tool_call)
            except Exception:
                logger.exception("pre-hook error")
                continue
            if result.blocked:
                tool_call.blocked = True
                tool_call.block_reason = result.reason
                return tool_call
            if result.modified_call is not None:
                tool_call = result.modified_call
        return tool_call

    async def run_post(self, agent_id: str, tool_call: ToolCall, result: Any) -> Any:
        for hook in self._post:
            try:
                result = await hook(agent_id, tool_call, result)
            except Exception:
                logger.exception("post-hook error")
        return result
