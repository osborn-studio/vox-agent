"""Tool Registry — extensible tool system (inspired by OpenHarness 43+ tools)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable["ToolResult"]]


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: ToolHandler
    requires_permission: bool = True


@dataclass
class ToolResult:
    output: str = ""
    error: str = ""
    success: bool = True


class ToolRegistry:
    """Central registry for all tools available to agents.

    Built-in tools (V1):
      read, write, edit, glob, grep, bash, web_search, web_fetch

    Extension points:
      - register() to add custom tools
      - MCP protocol (future)
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool
        logger.info("registered tool: %s", tool.name)

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    async def execute(self, name: str, input_data: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(error=f"unknown tool: {name}", success=False)
        try:
            return await tool.handler(input_data)
        except Exception as e:
            logger.exception("tool %s failed", name)
            return ToolResult(error=str(e), success=False)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def list_for_agent(self, allowed: set[str] | None = None) -> list[ToolDefinition]:
        if allowed is None:
            return self.list_tools()
        return [t for t in self._tools.values() if t.name in allowed]
