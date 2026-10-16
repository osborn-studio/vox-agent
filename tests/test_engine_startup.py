"""Startup integration test — verify the full server boots and responds."""

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def client(tmp_path):
    """Boot a complete app factory instance with isolated storage."""
    from engine.app_context import create_app_context
    from engine.server import create_app

    context = create_app_context(
        data_dir=tmp_path / "db",
        voice_memory_db=None,
    )
    context.store.connect()
    context.skill_loader.skills_dir = tmp_path / "skills"
    (tmp_path / "skills").mkdir()

    transport = ASGITransport(app=create_app(context))
    return AsyncClient(transport=transport, base_url="http://test")


class TestFullStartup:
    """Verify every major endpoint responds correctly."""

    @pytest.mark.asyncio
    async def test_health(self, client):
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_detailed_health(self, client):
        r = await client.get("/v1/health/detailed")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("healthy", "degraded")
        assert "tools" in data

    @pytest.mark.asyncio
    async def test_tools(self, client):
        r = await client.get("/v1/tools")
        assert r.status_code == 200
        names = {t["name"] for t in r.json()}
        assert names >= {"read", "write", "edit", "glob", "grep", "bash", "web_search", "web_fetch"}

    @pytest.mark.asyncio
    async def test_skills(self, client):
        r = await client.get("/v1/skills")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_profiles(self, client):
        r = await client.get("/v1/profiles")
        assert r.status_code == 200
        profiles = r.json()
        ids = {p["id"] for p in profiles}
        assert "coding" in ids
        assert "review" in ids

    @pytest.mark.asyncio
    async def test_templates(self, client):
        r = await client.get("/v1/templates")
        assert r.status_code == 200
        templates = r.json()
        ids = {t["id"] for t in templates}
        assert "code-review" in ids

    @pytest.mark.asyncio
    async def test_agents(self, client):
        r = await client.get("/v1/agents")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_tasks_empty(self, client):
        r = await client.get("/v1/tasks")
        assert r.status_code == 200
        assert r.json() == []

    @pytest.mark.asyncio
    async def test_analytics(self, client):
        r = await client.get("/v1/analytics")
        assert r.status_code == 200
        assert r.json()["total_tasks"] == 0

    @pytest.mark.asyncio
    async def test_costs(self, client):
        r = await client.get("/v1/costs")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_memory(self, client):
        r = await client.get("/v1/memory")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_schedules(self, client):
        r = await client.get("/v1/schedules")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_webhooks(self, client):
        r = await client.get("/v1/webhooks")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_heartbeat(self, client):
        r = await client.get("/v1/heartbeat")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_tags(self, client):
        r = await client.get("/v1/tags")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_validate(self, client):
        r = await client.get("/v1/validate")
        assert r.status_code == 200
        assert r.json()["valid"] is True

    @pytest.mark.asyncio
    async def test_engine_status(self, client):
        r = await client.get("/v1/engine/status")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_conversations(self, client):
        r = await client.get("/v1/conversations")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_plugins(self, client):
        r = await client.get("/v1/plugins")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_rate_limits(self, client):
        r = await client.get("/v1/rate-limits")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_workspaces(self, client):
        r = await client.get("/v1/workspaces")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_intent(self, client):
        r = await client.post("/v1/intent", json={"text": "帮我修 bug"})
        assert r.status_code == 200
        assert r.json()["intent"] == "task"

    @pytest.mark.asyncio
    async def test_message_record(self, client):
        r = await client.post("/v1/message", json={"text": "记一下：明天开会"})
        assert r.status_code == 200
        assert r.json()["action"] == "recorded"

    @pytest.mark.asyncio
    async def test_full_task_flow(self, client):
        """Create task → get → list → messages."""
        import asyncio

        # Create
        r = await client.post("/v1/tasks", json={
            "agent_id": "claude", "title": "test flow", "prompt": "hello",
        })
        assert r.status_code == 200
        tid = r.json()["task_id"]

        await asyncio.sleep(0.3)

        # Get
        r = await client.get(f"/v1/tasks/{tid}")
        assert r.status_code == 200

        # List
        r = await client.get("/v1/tasks")
        assert len(r.json()) >= 1

        # Messages
        r = await client.get(f"/v1/tasks/{tid}/messages")
        assert r.status_code == 200

        # Tags
        r = await client.post(f"/v1/tasks/{tid}/tags/test")
        assert r.status_code == 200

        # Comments
        r = await client.post(f"/v1/tasks/{tid}/comments",
                              json={"content": "Nice!"})
        assert r.status_code == 200
