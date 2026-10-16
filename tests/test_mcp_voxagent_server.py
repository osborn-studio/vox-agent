"""Tests for voxagent_mcp/voxagent_server.py — pure tool functions driven by
a fake API client (no real HTTP). `asyncio_mode = "auto"` (pyproject.toml)
lets us write plain `async def test_...` functions.
"""

from __future__ import annotations

import json

import pytest

from voxagent_mcp.voxagent_server import (
    TOOL_REGISTRY,
    create_capture_note,
    get_action_items,
    get_recent_context,
    get_session,
    get_token_balance,
    invoke_device_tool,
    list_devices,
    search_memory,
)


class FakeApiClient:
    """Canned camelCase responses matching the engine's HTTP API shapes.
    No httpx / no network — mirrors VoxAgentApiClient's async method names.
    """

    def __init__(self):
        self.calls = []

    async def search_memory(self, query, limit=10):
        self.calls.append(("search_memory", query, limit))
        return {
            "query": query,
            "results": [
                {
                    "node": {"id": "mem_1", "type": "DECISION", "title": "Ship mobile companion"},
                    "score": 1.0,
                    "highlights": ["We decided to ship it."],
                },
                {
                    "node": {"id": "mem_2", "type": "FACT", "title": "Second match"},
                    "score": 0.5,
                    "highlights": ["Some other text."],
                },
            ],
        }

    async def get_session(self, session_id):
        self.calls.append(("get_session", session_id))
        long_text = "A" * 500
        return {
            "session": {
                "id": session_id,
                "workspaceId": "ws_1",
                "title": "Weekly sync",
                "status": "READY",
                "source": "MOBILE",
                "mode": "MEETING",
                "startedAt": "2026-07-01T00:00:00+00:00",
                "endedAt": "2026-07-01T00:30:00+00:00",
            },
            "segments": [
                {"id": "seg_1", "speakerLabel": "A", "startMs": 0, "endMs": 100, "textNormalized": "Hello."},
                {"id": "seg_2", "speakerLabel": "B", "startMs": 100, "endMs": 200, "textNormalized": long_text},
                {"id": "seg_3", "speakerLabel": "A", "startMs": 200, "endMs": 300, "textNormalized": "Third."},
                {"id": "seg_4", "speakerLabel": "B", "startMs": 300, "endMs": 400, "textNormalized": "Fourth."},
                {"id": "seg_5", "speakerLabel": "A", "startMs": 400, "endMs": 500, "textNormalized": "Fifth."},
            ],
        }

    async def get_recent_context(self, days=7, limit=20):
        self.calls.append(("get_recent_context", days, limit))
        nodes = [
            {"id": f"mem_{i}", "type": "SUMMARY", "title": f"Node {i}", "createdAt": "2026-07-01T00:00:00+00:00"}
            for i in range(30)
        ]
        return nodes

    async def create_capture_note(self, title, body, tags=None, source=None):
        self.calls.append(("create_capture_note", title, body, tags, source))
        return {
            "id": "mem_new",
            "type": "FACT",
            "title": title,
            "body": body,
            "tags": tags or [],
            "createdAt": "2026-07-03T00:00:00+00:00",
        }

    async def list_devices(self):
        self.calls.append(("list_devices",))
        return [
            {
                "id": "dev_1",
                "kind": "SIMULATED_RECORDER",
                "displayName": "Test Recorder",
                "status": "CONNECTED",
                "batteryPercent": 88,
            }
        ]

    async def invoke_device_tool(self, device_id, tool, args=None):
        self.calls.append(("invoke_device_tool", device_id, tool, args))
        return {
            "id": "tool_1",
            "deviceId": device_id,
            "tool": tool,
            "args": args or {},
            "requestedAt": "2026-07-03T00:00:00+00:00",
            "status": "ACKNOWLEDGED",
        }

    async def get_token_balance(self):
        self.calls.append(("get_token_balance",))
        return {
            "scopeId": "user_local",
            "scopeType": "USER",
            "balance": 99000,
            "currency": "TOKENS",
            "updatedAt": "2026-07-03T00:00:00+00:00",
        }

    async def get_action_items(self, status=None):
        self.calls.append(("get_action_items", status))
        items = [
            {
                "id": "mem_action_1",
                "title": "Follow up with customer",
                "metadata": {"status": "OPEN"},
                "createdAt": "2026-07-03T00:00:00+00:00",
            }
        ]
        if status is not None:
            items = [i for i in items if i["metadata"]["status"] == status]
        return items


@pytest.fixture
def client():
    return FakeApiClient()


class TestSearchMemory:
    async def test_returns_structured_results(self, client):
        result = await search_memory(client, "mobile", limit=10)
        assert result["query"] == "mobile"
        assert result["count"] == 2
        assert result["results"][0]["id"] == "mem_1"
        assert result["results"][0]["title"] == "Ship mobile companion"
        assert "score" in result["results"][0]

    async def test_limit_passed_through(self, client):
        await search_memory(client, "mobile", limit=3)
        assert client.calls[0] == ("search_memory", "mobile", 3)

    async def test_result_bounded_by_limit(self, client):
        result = await search_memory(client, "mobile", limit=1)
        assert result["count"] == 1


class TestGetSession:
    async def test_bounded_session_no_full_transcript(self, client):
        result = await get_session(client, "cap_123")
        assert result["id"] == "cap_123"
        assert result["segmentCount"] == 5
        # Only first 3 segments previewed, not all 5.
        assert len(result["segmentPreviews"]) == 3
        assert [p["id"] for p in result["segmentPreviews"]] == ["seg_1", "seg_2", "seg_3"]

    async def test_long_segment_text_truncated(self, client):
        result = await get_session(client, "cap_123")
        long_preview = result["segmentPreviews"][1]["textNormalized"]
        assert len(long_preview) <= 201  # 200 chars + ellipsis
        assert long_preview.endswith("…")

    async def test_no_raw_transcript_key_present(self, client):
        result = await get_session(client, "cap_123")
        assert "segments" not in result
        assert "transcript" not in result

    async def test_session_metadata_included(self, client):
        result = await get_session(client, "cap_123")
        assert result["status"] == "READY"
        assert result["title"] == "Weekly sync"
        assert result["source"] == "MOBILE"


class TestGetRecentContext:
    async def test_bounded_by_limit(self, client):
        result = await get_recent_context(client, days=7, limit=5)
        assert result["count"] == 5
        assert len(result["nodes"]) == 5

    async def test_days_and_limit_passed_through(self, client):
        await get_recent_context(client, days=14, limit=10)
        assert client.calls[0] == ("get_recent_context", 14, 10)

    async def test_node_shape(self, client):
        result = await get_recent_context(client, days=7, limit=1)
        node = result["nodes"][0]
        assert set(node.keys()) == {"id", "type", "title", "createdAt"}


class TestCreateCaptureNote:
    async def test_creates_and_returns_summary(self, client):
        result = await create_capture_note(client, "Title", "Body text", tags=["a", "b"], source="mobile")
        assert result["id"] == "mem_new"
        assert result["title"] == "Title"
        assert result["tags"] == ["a", "b"]
        assert client.calls[0] == ("create_capture_note", "Title", "Body text", ["a", "b"], "mobile")


class TestListDevices:
    async def test_returns_device_summaries(self, client):
        result = await list_devices(client)
        assert result["count"] == 1
        assert result["devices"][0]["id"] == "dev_1"
        assert result["devices"][0]["status"] == "CONNECTED"


class TestInvokeDeviceTool:
    async def test_invokes_and_returns_status(self, client):
        result = await invoke_device_tool(client, "dev_1", "DEVICE_VIBRATE", {"pattern": "short"})
        assert result["deviceId"] == "dev_1"
        assert result["tool"] == "DEVICE_VIBRATE"
        assert result["status"] == "ACKNOWLEDGED"
        assert client.calls[0] == ("invoke_device_tool", "dev_1", "DEVICE_VIBRATE", {"pattern": "short"})


class TestGetTokenBalance:
    async def test_returns_balance(self, client):
        result = await get_token_balance(client)
        assert result["balance"] == 99000
        assert result["currency"] == "TOKENS"


class TestGetActionItems:
    async def test_no_filter(self, client):
        result = await get_action_items(client)
        assert result["count"] == 1
        assert result["items"][0]["id"] == "mem_action_1"
        assert result["items"][0]["status"] == "OPEN"

    async def test_status_filter_passed_through(self, client):
        await get_action_items(client, status="OPEN")
        assert client.calls[0] == ("get_action_items", "OPEN")

    async def test_status_filter_excludes_others(self, client):
        result = await get_action_items(client, status="DONE")
        assert result["count"] == 0


class TestToolRegistry:
    def test_registry_has_all_tool_names(self):
        expected = {
            "vox.search_memory",
            "vox.get_session",
            "vox.get_recent_context",
            "vox.create_capture_note",
            "vox.list_devices",
            "vox.invoke_device_tool",
            "vox.get_token_balance",
            "vox.get_action_items",
        }
        assert set(TOOL_REGISTRY.keys()) == expected

    def test_registry_values_are_callables(self):
        for fn in TOOL_REGISTRY.values():
            assert callable(fn)


class TestServeWithoutSdk:
    def test_serve_raises_clear_error_when_sdk_missing(self, monkeypatch):
        # Force the "SDK absent" branch deterministically (the real SDK may
        # or may not be installed in a given environment). serve() must fail
        # loudly with an actionable hint, not silently import the local
        # `voxagent_mcp` package.
        import voxagent_mcp.voxagent_server as srv

        monkeypatch.setattr(srv, "_load_mcp_sdk", lambda: None)
        with pytest.raises(RuntimeError, match="MCP SDK not installed"):
            srv.serve()

    def test_serve_builds_and_runs_when_sdk_present(self, monkeypatch):
        import voxagent_mcp.voxagent_server as srv

        if srv._load_mcp_sdk() is None:
            pytest.skip("mcp SDK not installed")

        ran = {}
        real_build = srv.build_mcp_server

        def spy_build(client, *a, **k):
            app = real_build(client, *a, **k)
            monkeypatch.setattr(app, "run", lambda *a, **k: ran.setdefault("ran", True))
            return app

        monkeypatch.setattr(srv, "build_mcp_server", spy_build)
        srv.serve()
        assert ran.get("ran") is True


class TestBuildMcpServer:
    async def test_registers_all_eight_vox_tools(self):
        from voxagent_mcp.voxagent_server import TOOL_REGISTRY, build_mcp_server

        app = build_mcp_server(client=FakeApiClient())
        tools = await app.list_tools()
        names = {t.name for t in tools}
        assert names == set(TOOL_REGISTRY.keys())
        assert len(names) == 8

    async def test_search_memory_tool_has_query_param_in_schema(self):
        from voxagent_mcp.voxagent_server import build_mcp_server

        app = build_mcp_server(client=FakeApiClient())
        tools = {t.name: t for t in await app.list_tools()}
        schema = tools["vox.search_memory"].inputSchema
        assert "query" in schema.get("properties", {})

    async def test_call_tool_routes_through_client(self):
        from voxagent_mcp.voxagent_server import build_mcp_server

        fake = FakeApiClient()  # returns canned search results
        app = build_mcp_server(client=fake)
        result = await app.call_tool("vox.search_memory", {"query": "mobile", "limit": 5})

        # In mcp 1.28.1, FastMCP.call_tool returns Sequence[ContentBlock] |
        # dict[str, Any]; a bare `-> dict` return annotation on the wrapper
        # does NOT generate a pydantic output_schema, so no structured
        # content tuple is produced — the result is a plain list containing
        # one TextContent whose `.text` is the JSON-serialized dict our pure
        # tool function returned. Assert on that concrete shape.
        assert isinstance(result, list)
        assert len(result) == 1
        content = result[0]
        assert content.type == "text"
        payload = json.loads(content.text)
        assert payload["count"] == 2
        assert payload["results"][0]["id"] == "mem_1"

        # And prove the wrapper actually reached the fake client.
        assert fake.calls[0] == ("search_memory", "mobile", 5)
