"""File operation tools — read, write, edit, glob, grep."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import ToolDefinition, ToolResult

if TYPE_CHECKING:
    from . import ToolRegistry


async def _read(params: dict[str, Any]) -> ToolResult:
    path = params.get("file_path", "")
    if not path:
        return ToolResult(error="file_path required", success=False)
    try:
        text = Path(path).expanduser().read_text(errors="replace")
        limit = params.get("limit")
        offset = params.get("offset", 0)
        if limit:
            lines = text.splitlines(keepends=True)
            text = "".join(lines[offset : offset + limit])
        return ToolResult(output=text)
    except FileNotFoundError:
        return ToolResult(error=f"file not found: {path}", success=False)
    except Exception as e:
        return ToolResult(error=str(e), success=False)


async def _write(params: dict[str, Any]) -> ToolResult:
    path = params.get("file_path", "")
    content = params.get("content", "")
    if not path:
        return ToolResult(error="file_path required", success=False)
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return ToolResult(output=f"wrote {len(content)} bytes to {path}")
    except Exception as e:
        return ToolResult(error=str(e), success=False)


async def _edit(params: dict[str, Any]) -> ToolResult:
    path = params.get("file_path", "")
    old = params.get("old_string", "")
    new = params.get("new_string", "")
    if not path or not old:
        return ToolResult(error="file_path and old_string required", success=False)
    try:
        p = Path(path).expanduser()
        text = p.read_text()
        if old not in text:
            return ToolResult(error="old_string not found in file", success=False)
        if params.get("replace_all"):
            text = text.replace(old, new)
        else:
            text = text.replace(old, new, 1)
        p.write_text(text)
        return ToolResult(output=f"edited {path}")
    except Exception as e:
        return ToolResult(error=str(e), success=False)


async def _glob(params: dict[str, Any]) -> ToolResult:
    pattern = params.get("pattern", "")
    base = params.get("path", ".")
    if not pattern:
        return ToolResult(error="pattern required", success=False)
    try:
        base_path = Path(base).expanduser().resolve()
        matches = sorted(str(p) for p in base_path.rglob(pattern))
        return ToolResult(output="\n".join(matches[:200]))
    except Exception as e:
        return ToolResult(error=str(e), success=False)


async def _grep(params: dict[str, Any]) -> ToolResult:
    pattern = params.get("pattern", "")
    path = params.get("path", ".")
    if not pattern:
        return ToolResult(error="pattern required", success=False)
    try:
        regex = re.compile(pattern, re.IGNORECASE if params.get("ignore_case") else 0)
        base = Path(path).expanduser().resolve()
        results: list[str] = []
        glob_filter = params.get("glob", "*")

        if base.is_file():
            files = [base]
        else:
            files = list(base.rglob(glob_filter))

        for f in files[:500]:
            if not f.is_file():
                continue
            try:
                for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{f}:{i}:{line}")
                        if len(results) >= 100:
                            break
            except Exception:
                continue
            if len(results) >= 100:
                break

        return ToolResult(output="\n".join(results) if results else "no matches")
    except Exception as e:
        return ToolResult(error=str(e), success=False)


def register_file_tools(registry: "ToolRegistry") -> None:
    registry.register(ToolDefinition(
        name="read",
        description="Read a file from disk",
        parameters={"file_path": "string", "limit": "int?", "offset": "int?"},
        handler=_read,
        requires_permission=False,
    ))
    registry.register(ToolDefinition(
        name="write",
        description="Write content to a file",
        parameters={"file_path": "string", "content": "string"},
        handler=_write,
    ))
    registry.register(ToolDefinition(
        name="edit",
        description="Replace text in a file",
        parameters={"file_path": "string", "old_string": "string", "new_string": "string"},
        handler=_edit,
    ))
    registry.register(ToolDefinition(
        name="glob",
        description="Find files by glob pattern",
        parameters={"pattern": "string", "path": "string?"},
        handler=_glob,
        requires_permission=False,
    ))
    registry.register(ToolDefinition(
        name="grep",
        description="Search file contents by regex",
        parameters={"pattern": "string", "path": "string?", "glob": "string?"},
        handler=_grep,
        requires_permission=False,
    ))
