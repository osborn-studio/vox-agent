"""End-to-end integration test — API + scheduler + engine + WebSocket events."""

import asyncio
import pytest

from httpx import AsyncClient, ASGITransport

from engine import server as srv
from engine.app_context import create_app_context
from engine.models import EngineEvent, EventType


@pytest.fixture
def context(tmp_path):
    """Fresh application context and skill directory per test."""
    isolated = create_app_context(data_dir=tmp_path, voice_memory_db=None)
    isolated.store.connect()
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    isolated.skill_loader.skills_dir = skills_dir
    yield isolated
    isolated.close()


@pytest.fixture
def client(context):
    return AsyncClient(
        transport=ASGITransport(app=srv.create_app(context)),
        base_url="http://test",
    )


class TestFullPipeline:
    """Test the complete flow: create task → engine executes → messages stored → events emitted."""

    @pytest.mark.asyncio
    async def test_task_lifecycle_with_mock_engine(self, client, context):
        """Swap in a mock engine and verify the full lifecycle."""

        # Mock engine that simulates a 3-step task
        async def mock_run(agent_id, prompt, **kwargs):
            yield EngineEvent(type=EventType.TEXT_DELTA, data="Analyzing request...")
            yield EngineEvent(type=EventType.TOOL_USE, tool="read", input={"file_path": "/tmp/test.py"})
            yield EngineEvent(type=EventType.TOOL_DONE, tool="read", output="def hello(): pass")
            yield EngineEvent(type=EventType.TEXT_DELTA, data="File looks good. Done.")
            yield EngineEvent(type=EventType.DONE, result={"summary": "all clear"})

        context.scheduler.engine.run = mock_run

        # Subscribe to WebSocket events
        ws_queue = context.event_bus.subscribe_ws()

        # Create task via API
        r = await client.post("/v1/tasks", json={
            "agent_id": "claude-code",
            "title": "Review test.py",
            "prompt": "Review the test.py file for issues",
            "priority": 2,
        })
        assert r.status_code == 200
        task_id = r.json()["task_id"]

        # Wait for async execution
        await asyncio.sleep(1.0)

        # Verify task completed
        r = await client.get(f"/v1/tasks/{task_id}")
        assert r.json()["status"] == "completed"

        # Verify messages stored
        r = await client.get(f"/v1/tasks/{task_id}/messages")
        msgs = r.json()
        assert len(msgs) >= 4
        types = [m["type"] for m in msgs]
        assert "text" in types
        assert "tool_use" in types
        assert "tool_result" in types

        # Verify message sequence
        seqs = [m["seq"] for m in msgs]
        assert seqs == sorted(seqs)

        # Verify WebSocket events were emitted
        ws_events = []
        while not ws_queue.empty():
            ws_events.append(ws_queue.get_nowait())

        ws_types = {e["type"] for e in ws_events}
        assert "task:created" in ws_types
        assert "task:message" in ws_types
        assert "task:completed" in ws_types

        context.event_bus.unsubscribe_ws(ws_queue)

    @pytest.mark.asyncio
    async def test_multiple_concurrent_tasks(self, client, context):
        """Two tasks running concurrently should not interfere."""

        call_count = {"a": 0, "b": 0}

        async def mock_run(agent_id, prompt, **kwargs):
            call_count[agent_id] += 1
            yield EngineEvent(type=EventType.TEXT_DELTA, data=f"Agent {agent_id} working")
            await asyncio.sleep(0.1)
            yield EngineEvent(type=EventType.DONE, result={"agent": agent_id})

        context.scheduler.engine.run = mock_run

        # Create two tasks simultaneously
        r1 = await client.post("/v1/tasks", json={
            "agent_id": "a", "title": "task A", "prompt": "do A",
        })
        r2 = await client.post("/v1/tasks", json={
            "agent_id": "b", "title": "task B", "prompt": "do B",
        })

        await asyncio.sleep(1.0)

        # Both should complete
        t1 = await client.get(f"/v1/tasks/{r1.json()['task_id']}")
        t2 = await client.get(f"/v1/tasks/{r2.json()['task_id']}")
        assert t1.json()["status"] == "completed"
        assert t2.json()["status"] == "completed"
        assert call_count == {"a": 1, "b": 1}

    @pytest.mark.asyncio
    async def test_skill_loaded_from_file(self, client, tmp_path):
        """Skills loaded from disk should appear in API."""
        skills_dir = tmp_path / "skills"
        (skills_dir / "analyzer.md").write_text(
            "---\n"
            "name: analyzer\n"
            "description: Analyze code patterns\n"
            "tools: [read, grep]\n"
            "---\n"
            "You analyze code patterns and report findings."
        )

        r = await client.get("/v1/skills")
        skills = r.json()
        assert any(s["name"] == "analyzer" for s in skills)

    @pytest.mark.asyncio
    async def test_task_with_engine_failure_recovers(self, client, context):
        """Engine failure should not crash the server."""

        async def failing_engine(agent_id, prompt, **kwargs):
            yield EngineEvent(type=EventType.TEXT_DELTA, data="starting...")
            raise ConnectionError("LLM provider unreachable")

        context.scheduler.engine.run = failing_engine

        r = await client.post("/v1/tasks", json={
            "agent_id": "claude", "title": "will fail", "prompt": "test",
        })
        task_id = r.json()["task_id"]
        await asyncio.sleep(0.5)

        # Task should be failed, not crashed
        r = await client.get(f"/v1/tasks/{task_id}")
        assert r.json()["status"] == "failed"

        # Server should still be healthy
        r = await client.get("/health")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_tools_endpoint_returns_8(self, client):
        """All 8 built-in tools should be registered."""
        r = await client.get("/v1/tools")
        names = {t["name"] for t in r.json()}
        expected = {"read", "write", "edit", "glob", "grep", "bash", "web_search", "web_fetch"}
        assert names == expected

    @pytest.mark.asyncio
    async def test_task_list_filtering(self, client, context):
        """Task filtering by agent and status should work."""
        async def instant_done(agent_id, prompt, **kwargs):
            yield EngineEvent(type=EventType.DONE, result={})

        context.scheduler.engine.run = instant_done

        # Create tasks for different agents
        await client.post("/v1/tasks", json={
            "agent_id": "claude", "title": "a", "prompt": "x",
        })
        await client.post("/v1/tasks", json={
            "agent_id": "codex", "title": "b", "prompt": "y",
        })
        await asyncio.sleep(0.5)

        # Filter by agent
        r = await client.get("/v1/tasks?agent_id=claude")
        assert len(r.json()) == 1

        # Filter by status
        r = await client.get("/v1/tasks?status=completed")
        assert len(r.json()) == 2
