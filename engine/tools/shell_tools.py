"""Shell tool — execute bash commands."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from . import ToolDefinition, ToolResult

if TYPE_CHECKING:
    from . import ToolRegistry


async def _bash(params: dict[str, Any]) -> ToolResult:
    command = params.get("command", "")
    if not command:
        return ToolResult(error="command required", success=False)

    timeout = min(params.get("timeout", 120), 600)  # cap at 10 min

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        output = stdout.decode(errors="replace")
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            return ToolResult(
                output=output,
                error=f"exit code {proc.returncode}: {err}",
                success=False,
            )
        return ToolResult(output=output)
    except asyncio.TimeoutError:
        proc.kill()
        return ToolResult(error=f"command timed out after {timeout}s", success=False)
    except Exception as e:
        return ToolResult(error=str(e), success=False)


def register_shell_tools(registry: "ToolRegistry") -> None:
    registry.register(ToolDefinition(
        name="bash",
        description="Execute a bash command",
        parameters={"command": "string", "timeout": "int?"},
        handler=_bash,
    ))
