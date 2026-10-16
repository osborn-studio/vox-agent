"""VOXAGENT MCP tool server — thin client over the engine's voice-memory,
device, and token HTTP API.

NAMING NOTE: this module lives in the `voxagent_mcp` package — deliberately
NOT `mcp/` as the original build plan named it. The repo root precedes
site-packages on `sys.path` whenever this repo's own code/tests run, so a
package literally named `mcp` would permanently shadow the upstream Model
Context Protocol Python SDK (`pip install mcp`): a bare `import mcp` would
always resolve to the local package instead of the SDK, even after the SDK
is pip-installed, leaving `serve()` (below) dead on arrival. Renaming this
package to `voxagent_mcp` removes the collision, so:

  * The pure tool functions and `VoxAgentApiClient` below have zero
    dependency on the SDK and are fully testable without it (see
    tests/test_mcp_voxagent_server.py).
  * `serve()` builds a real `mcp.server.fastmcp.FastMCP` server via
    `build_mcp_server()` and calls `app.run()`. The `mcp` SDK is an
    OPTIONAL dependency (the `mcp` extra in pyproject.toml — `pip install
    '.[mcp]'`); `_load_mcp_sdk()` guards every SDK import so that when the
    SDK is absent, `serve()` prints a clear, actionable install hint and
    raises `RuntimeError` instead of crashing into a raw traceback.

Design mirrors the plan (VOXAGENT Mobile Companion, Task 7 / build
instructions §8): pure async functions taking `(client, ...)` and returning
JSON-compatible dicts, wrapped by `build_mcp_server()` into MCP tools named
`vox.search_memory`, `vox.get_session`, `vox.get_recent_context`,
`vox.create_capture_note`, `vox.list_devices`, `vox.invoke_device_tool`,
`vox.get_token_balance`, and `vox.get_action_items`.

Bounded output requirement: `get_session` never returns the full transcript.
It returns session metadata plus a segment count and a small number of
truncated segment previews. `search_memory` / `get_recent_context` respect
their `limit` parameters. Every tool returns IDs and short summaries, never
unbounded text.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:18900"

# How many transcript segments to preview in get_session, and how long each
# preview's normalized text is truncated to. Keeps tool output bounded no
# matter how long the underlying capture session is.
_SESSION_PREVIEW_SEGMENT_COUNT = 3
_SESSION_PREVIEW_TEXT_CHARS = 200


def _truncate(text: str | None, limit: int = _SESSION_PREVIEW_TEXT_CHARS) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


class VoxAgentApiClient:
    """Async HTTP client over the vox-engine's REST API.

    Matches the base-URL convention used by `engine/cli.py`'s
    `_run_client_command` (`http://127.0.0.1:18900`, the engine's default
    CLI start port). One `httpx.AsyncClient` per call, per the plan.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def search_memory(self, query: str, limit: int = 10) -> dict:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            r = await client.get("/v1/memory/search", params={"q": query, "limit": limit})
            r.raise_for_status()
            return r.json()

    async def get_session(self, session_id: str) -> dict:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            r = await client.get(f"/v1/capture/sessions/{session_id}")
            r.raise_for_status()
            session = r.json()
            r2 = await client.get(f"/v1/capture/sessions/{session_id}/transcript-segments")
            r2.raise_for_status()
            segments = r2.json()
        return {"session": session, "segments": segments}

    async def get_recent_context(self, days: int = 7, limit: int = 20) -> list:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            r = await client.get("/v1/memory/recent", params={"days": days, "limit": limit})
            r.raise_for_status()
            return r.json()

    async def create_capture_note(
        self,
        title: str,
        body: str,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "type": "FACT",
            "title": title,
            "body": body,
            "tags": tags or [],
        }
        if source is not None:
            payload["metadata"] = {"source": source}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            r = await client.post("/v1/memory/nodes", json=payload)
            r.raise_for_status()
            return r.json()

    async def list_devices(self) -> list:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            r = await client.get("/v1/devices")
            r.raise_for_status()
            return r.json()

    async def invoke_device_tool(self, device_id: str, tool: str, args: dict | None = None) -> dict:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            r = await client.post(
                f"/v1/devices/{device_id}/tools",
                json={"tool": tool, "args": args or {}},
            )
            r.raise_for_status()
            return r.json()

    async def get_token_balance(self) -> dict:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            r = await client.get("/v1/tokens/balance")
            r.raise_for_status()
            return r.json()

    async def get_action_items(self, status: str | None = None) -> list:
        params = {"status": status} if status else {}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            r = await client.get("/v1/memory/action-items", params=params)
            r.raise_for_status()
            return r.json()


# ---------------------------------------------------------------------------
# Pure tool functions — (client, ...) -> dict. No MCP SDK dependency here.
# ---------------------------------------------------------------------------


async def search_memory(client: Any, query: str, limit: int = 10) -> dict:
    """Search memory nodes. Respects `limit`; returns IDs + short summaries."""
    result = await client.search_memory(query, limit=limit)
    results = result.get("results", [])[:limit]
    return {
        "query": result.get("query", query),
        "count": len(results),
        "results": [
            {
                "id": item["node"]["id"],
                "type": item["node"].get("type"),
                "title": item["node"].get("title"),
                "score": item.get("score"),
                "highlights": item.get("highlights", [])[:1],
            }
            for item in results
        ],
    }


async def get_session(client: Any, session_id: str) -> dict:
    """Fetch a capture session's metadata + bounded segment previews.

    Never returns the full transcript: only `segmentCount` plus at most
    `_SESSION_PREVIEW_SEGMENT_COUNT` previews, each truncated to
    `_SESSION_PREVIEW_TEXT_CHARS` characters.
    """
    data = await client.get_session(session_id)
    session = data["session"]
    segments = data.get("segments", [])
    previews = [
        {
            "id": seg.get("id"),
            "speakerLabel": seg.get("speakerLabel"),
            "startMs": seg.get("startMs"),
            "endMs": seg.get("endMs"),
            "textNormalized": _truncate(seg.get("textNormalized")),
        }
        for seg in segments[:_SESSION_PREVIEW_SEGMENT_COUNT]
    ]
    return {
        "id": session.get("id"),
        "workspaceId": session.get("workspaceId"),
        "title": session.get("title"),
        "status": session.get("status"),
        "source": session.get("source"),
        "mode": session.get("mode"),
        "startedAt": session.get("startedAt"),
        "endedAt": session.get("endedAt"),
        "segmentCount": len(segments),
        "segmentPreviews": previews,
    }


async def get_recent_context(client: Any, days: int = 7, limit: int = 20) -> dict:
    """Recent memory nodes (bounded by `limit`), as IDs + summaries."""
    nodes = await client.get_recent_context(days=days, limit=limit)
    nodes = nodes[:limit]
    return {
        "days": days,
        "count": len(nodes),
        "nodes": [
            {
                "id": n["id"],
                "type": n.get("type"),
                "title": n.get("title"),
                "createdAt": n.get("createdAt"),
            }
            for n in nodes
        ],
    }


async def create_capture_note(
    client: Any,
    title: str,
    body: str,
    tags: list[str] | None = None,
    source: str | None = None,
) -> dict:
    """Create a memory node of type FACT from title/body/tags."""
    node = await client.create_capture_note(title=title, body=body, tags=tags, source=source)
    return {
        "id": node["id"],
        "type": node.get("type"),
        "title": node.get("title"),
        "tags": node.get("tags", []),
        "createdAt": node.get("createdAt"),
    }


async def list_devices(client: Any) -> dict:
    """List paired devices as IDs + short summaries."""
    devices = await client.list_devices()
    return {
        "count": len(devices),
        "devices": [
            {
                "id": d["id"],
                "kind": d.get("kind"),
                "displayName": d.get("displayName"),
                "status": d.get("status"),
                "batteryPercent": d.get("batteryPercent"),
            }
            for d in devices
        ],
    }


async def invoke_device_tool(client: Any, device_id: str, tool: str, args: dict | None = None) -> dict:
    """Invoke a tool call on a paired device."""
    result = await client.invoke_device_tool(device_id, tool, args or {})
    return {
        "id": result.get("id"),
        "deviceId": result.get("deviceId"),
        "tool": result.get("tool"),
        "status": result.get("status"),
        "requestedAt": result.get("requestedAt"),
    }


async def get_token_balance(client: Any) -> dict:
    """Current token balance for the local scope."""
    balance = await client.get_token_balance()
    return {
        "scopeId": balance.get("scopeId"),
        "scopeType": balance.get("scopeType"),
        "balance": balance.get("balance"),
        "currency": balance.get("currency"),
        "updatedAt": balance.get("updatedAt"),
    }


async def get_action_items(client: Any, status: str | None = None) -> dict:
    """List action-item memory nodes, optionally filtered by status."""
    items = await client.get_action_items(status=status)
    return {
        "status": status,
        "count": len(items),
        "items": [
            {
                "id": n["id"],
                "title": n.get("title"),
                "status": n.get("metadata", {}).get("status"),
                "createdAt": n.get("createdAt"),
            }
            for n in items
        ],
    }


# ---------------------------------------------------------------------------
# MCP SDK wrapper — lazily imported so this module is importable (and fully
# testable) without the SDK installed.
# ---------------------------------------------------------------------------

_MCP_INSTALL_HINT = (
    "MCP SDK not installed — pip install mcp\n"
    "(the VOXAGENT tool functions in voxagent_mcp/voxagent_server.py work standalone; "
    "only `serve()` needs the SDK to expose them over the MCP protocol.)"
)


def _load_mcp_sdk():
    """Import the real `mcp` SDK's `mcp.server` module.

    Returns the imported module, or None if the SDK isn't installed (it's
    an optional dependency — see the `mcp` extra in pyproject.toml). Now
    that this package is named `voxagent_mcp` (not `mcp`), a plain import is
    safe: there's no local package left to shadow the SDK.
    """
    try:
        import mcp.server
    except ImportError:
        return None
    return mcp.server


TOOL_REGISTRY: dict[str, Any] = {
    "vox.search_memory": search_memory,
    "vox.get_session": get_session,
    "vox.get_recent_context": get_recent_context,
    "vox.create_capture_note": create_capture_note,
    "vox.list_devices": list_devices,
    "vox.invoke_device_tool": invoke_device_tool,
    "vox.get_token_balance": get_token_balance,
    "vox.get_action_items": get_action_items,
}


def build_mcp_server(client: "VoxAgentApiClient | Any", name: str = "voxagent"):
    """Construct a FastMCP server exposing the 8 vox.* tools, bound to `client`.

    Wrappers carry explicit signatures so FastMCP derives correct input
    schemas (a generic *args/**kwargs wrapper yields empty schemas). Raises
    RuntimeError with an install hint when the SDK is missing.
    """
    if _load_mcp_sdk() is None:
        raise RuntimeError(_MCP_INSTALL_HINT)
    from mcp.server.fastmcp import FastMCP

    app = FastMCP(name)

    @app.tool(name="vox.search_memory", description="Search voice memory nodes.")
    async def _search_memory(query: str, limit: int = 10) -> dict:
        return await search_memory(client, query, limit=limit)

    @app.tool(name="vox.get_session", description="Get a capture session with bounded segment previews.")
    async def _get_session(session_id: str) -> dict:
        return await get_session(client, session_id)

    @app.tool(name="vox.get_recent_context", description="List recent memory nodes.")
    async def _get_recent_context(days: int = 7, limit: int = 20) -> dict:
        return await get_recent_context(client, days=days, limit=limit)

    @app.tool(name="vox.create_capture_note", description="Create a FACT memory node.")
    async def _create_capture_note(
        title: str, body: str, tags: list[str] | None = None, source: str | None = None
    ) -> dict:
        return await create_capture_note(client, title=title, body=body, tags=tags, source=source)

    @app.tool(name="vox.list_devices", description="List paired devices.")
    async def _list_devices() -> dict:
        return await list_devices(client)

    @app.tool(name="vox.invoke_device_tool", description="Invoke a tool on a paired device.")
    async def _invoke_device_tool(device_id: str, tool: str, args: dict | None = None) -> dict:
        return await invoke_device_tool(client, device_id, tool, args or {})

    @app.tool(name="vox.get_token_balance", description="Get the local token balance.")
    async def _get_token_balance() -> dict:
        return await get_token_balance(client)

    @app.tool(name="vox.get_action_items", description="List action-item memory nodes.")
    async def _get_action_items(status: str | None = None) -> dict:
        return await get_action_items(client, status=status)

    return app


def serve(base_url: str = DEFAULT_BASE_URL, transport: str = "stdio") -> None:
    """Start the MCP server over the given transport (default stdio).

    Builds the FastMCP server via build_mcp_server and runs it. Raises
    RuntimeError with an actionable install hint if the `mcp` SDK is not
    installed (the pure tool functions above work without it).
    """
    if _load_mcp_sdk() is None:
        print(_MCP_INSTALL_HINT)
        raise RuntimeError(_MCP_INSTALL_HINT)
    client = VoxAgentApiClient(base_url=base_url)
    app = build_mcp_server(client)
    app.run(transport=transport)
