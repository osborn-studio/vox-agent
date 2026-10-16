"""Integration tests for engine.server — full API surface."""

import asyncio
import pytest

from httpx import AsyncClient, ASGITransport

from engine import server as srv
from engine.app_context import create_app_context
from engine.models import EngineEvent, EventType
from engine.permissions import ToolCall


@pytest.fixture
def context(tmp_path):
    """Each test gets its own complete application context."""
    isolated = create_app_context(data_dir=tmp_path, voice_memory_db=None)
    isolated.store.connect()

    async def instant_done(agent_id, prompt, **kwargs):
        yield EngineEvent(type=EventType.DONE, result={})

    isolated.scheduler.engine.run = instant_done
    yield isolated
    isolated.close()


@pytest.fixture
def client(context):
    transport = ASGITransport(app=srv.create_app(context))
    return AsyncClient(transport=transport, base_url="http://test")


# -- Health & Status ---------------------------------------------------------

class TestHealth:
    @pytest.mark.asyncio
    async def test_health(self, client):
        r = await client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "active_tasks" in data

    @pytest.mark.asyncio
    async def test_engine_status(self, client):
        r = await client.get("/v1/engine/status")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# -- Tools -------------------------------------------------------------------

class TestToolsAPI:
    @pytest.mark.asyncio
    async def test_list_tools(self, client):
        r = await client.get("/v1/tools")
        assert r.status_code == 200
        tools = r.json()
        names = {t["name"] for t in tools}
        assert "read" in names
        assert "bash" in names
        assert "web_search" in names
        assert "web_fetch" in names
        assert len(tools) == 8


# -- Skills ------------------------------------------------------------------

class TestSkillsAPI:
    @pytest.mark.asyncio
    async def test_list_skills_empty(self, client):
        r = await client.get("/v1/skills")
        assert r.status_code == 200
        assert r.json() == []

    @pytest.mark.asyncio
    async def test_list_skills_with_file(self, client, context, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "test-skill.md").write_text(
            "---\nname: test-skill\ndescription: A test skill\ntools: [read, grep]\n---\nYou are a test agent."
        )
        context.skill_loader.skills_dir = skills_dir
        r = await client.get("/v1/skills")
        assert r.status_code == 200
        skills = r.json()
        assert len(skills) == 1
        assert skills[0]["name"] == "test-skill"
        assert "read" in skills[0]["tools"]


# -- Tasks CRUD --------------------------------------------------------------

class TestTasksAPI:
    @pytest.mark.asyncio
    async def test_create_task_preserves_valid_client_task_id(self, client):
        r = await client.post("/v1/tasks", json={
            "task_id": "swift-task-123",
            "agent_id": "claude-code",
            "title": "Stable identity",
            "prompt": "run",
        })

        assert r.status_code == 200
        assert r.json()["task_id"] == "swift-task-123"

    @pytest.mark.asyncio
    async def test_create_task_rejects_unsafe_client_task_id(self, client):
        r = await client.post("/v1/tasks", json={
            "task_id": "../../unsafe id",
            "agent_id": "claude-code",
            "title": "Invalid identity",
            "prompt": "run",
        })

        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_client_task_id_is_idempotent_and_rejects_conflicts(self, client):
        payload = {
            "task_id": "swift-idempotent-123",
            "agent_id": "claude-code",
            "title": "Stable identity",
            "prompt": "run",
            "provider": "noop",
        }
        first = await client.post("/v1/tasks", json=payload)
        second = await client.post("/v1/tasks", json=payload)
        conflict = await client.post("/v1/tasks", json={
            **payload,
            "title": "Different task",
        })

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["task_id"] == "swift-idempotent-123"
        assert conflict.status_code == 409
        tasks = await client.get("/v1/tasks")
        assert [task["id"] for task in tasks.json()].count("swift-idempotent-123") == 1

    @pytest.mark.asyncio
    async def test_create_task(self, client):
        r = await client.post("/v1/tasks", json={
            "agent_id": "claude-code",
            "title": "Write tests",
            "prompt": "write unit tests",
        })
        assert r.status_code == 200
        data = r.json()
        assert "task_id" in data
        assert data["status"] == "draft"

    @pytest.mark.asyncio
    async def test_get_task(self, client):
        r1 = await client.post("/v1/tasks", json={
            "agent_id": "claude", "title": "test", "prompt": "test",
        })
        tid = r1.json()["task_id"]
        await asyncio.sleep(0.3)
        r2 = await client.get(f"/v1/tasks/{tid}")
        assert r2.status_code == 200
        assert r2.json()["id"] == tid

    @pytest.mark.asyncio
    async def test_get_nonexistent_task(self, client):
        r = await client.get("/v1/tasks/nonexistent")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_list_tasks(self, client):
        for i in range(3):
            await client.post("/v1/tasks", json={
                "agent_id": "claude", "title": f"task-{i}", "prompt": "x",
            })
        await asyncio.sleep(0.1)
        r = await client.get("/v1/tasks")
        assert r.status_code == 200
        assert len(r.json()) == 3

    @pytest.mark.asyncio
    async def test_list_tasks_by_agent(self, client):
        await client.post("/v1/tasks", json={
            "agent_id": "claude", "title": "a", "prompt": "x",
        })
        await client.post("/v1/tasks", json={
            "agent_id": "codex", "title": "b", "prompt": "x",
        })
        await asyncio.sleep(0.1)
        r = await client.get("/v1/tasks?agent_id=claude")
        assert len(r.json()) == 1

    @pytest.mark.asyncio
    async def test_get_task_messages_empty(self, client):
        r1 = await client.post("/v1/tasks", json={
            "agent_id": "claude", "title": "test", "prompt": "test",
            "provider": "noop",
        })
        tid = r1.json()["task_id"]
        r2 = await client.get(f"/v1/tasks/{tid}/messages")
        assert r2.status_code == 200
        assert isinstance(r2.json(), list)

    @pytest.mark.asyncio
    async def test_create_task_with_priority(self, client):
        r = await client.post("/v1/tasks", json={
            "agent_id": "claude", "title": "urgent", "prompt": "fix now",
            "priority": 3,
        })
        assert r.status_code == 200
        await asyncio.sleep(0.1)
        tid = r.json()["task_id"]
        r2 = await client.get(f"/v1/tasks/{tid}")
        assert r2.json()["priority"] == 3


# -- Cancel ------------------------------------------------------------------

class TestCancelAPI:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, client):
        r = await client.post("/v1/tasks/nope/cancel")
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_pause_and_resume_nonexistent(self, client):
        paused = await client.post("/v1/tasks/nope/pause")
        resumed = await client.post("/v1/tasks/nope/resume")

        assert paused.status_code == 400
        assert resumed.status_code == 400


# -- Permissions -------------------------------------------------------------

class TestPermissionsAPI:
    @pytest.mark.asyncio
    async def test_approval_round_trip_uses_websocket_event_and_rest_decision(
        self, client, context
    ):
        queue = context.event_bus.subscribe_ws()
        check_task = asyncio.create_task(context.permission_manager.check(
            ToolCall(tool="bash", input={"command": "git status --short"}),
            task_id="task-123",
            agent_id="codex",
        ))

        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.3)
            assert event["type"] == "permission:request"
            assert event["payload"]["task_id"] == "task-123"
            assert event["payload"]["agent_id"] == "codex"
            assert event["payload"]["scope"] == {
                "command": "git status --short",
            }

            response = await client.post("/v1/permissions/approve", json={
                "approval_id": event["payload"]["approval_id"],
                "approved": True,
            })
            assert response.status_code == 200
            assert response.json() == {"ok": True, "decision": "approved"}
            assert await asyncio.wait_for(check_task, timeout=0.3) is True
        finally:
            context.event_bus.unsubscribe_ws(queue)
            if not check_task.done():
                check_task.cancel()

    @pytest.mark.asyncio
    async def test_unknown_approval_is_rejected(self, client):
        response = await client.post("/v1/permissions/approve", json={
            "approval_id": "missing",
            "approved": True,
        })

        assert response.status_code == 404

@pytest.mark.asyncio
async def test_explicit_repository_execution_bypasses_intent_and_keeps_directory(client, context, tmp_path):
    repository = tmp_path / "repo with spaces"
    repository.mkdir()
    (repository / ".git").write_text("gitdir: /unused/worktree")
    seen = []

    async def capture(agent_id, prompt, **kwargs):
        seen.append(kwargs)
        yield EngineEvent(type=EventType.DONE, result={})

    context.scheduler.engine.run = capture
    response = await client.post("/v1/message", json={
        "text": "记录一下今天的工作", "agent_id": "code-master",
        "working_directory": str(repository),
    })
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    await context.scheduler._dispatch_tasks[task_id]
    assert seen[0]["working_directory"] == str(repository)
    row = context.store.get_task(task_id)
    assert row["working_directory"] == str(repository)
    assert row["requester_user_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["relative", "missing", "ordinary", "bundle", "symlink_bundle"])
async def test_repository_execution_rejects_invalid_directory(client, context, tmp_path, kind):
    directory = tmp_path / "ordinary"
    directory.mkdir()
    if kind in {"bundle", "symlink_bundle"}:
        directory = tmp_path / "Unsafe.app" / "repo"
        directory.mkdir(parents=True)
        (directory / ".git").mkdir()
        if kind == "symlink_bundle":
            link = tmp_path / "alias"
            link.symlink_to(directory)
            directory = link
    path = "relative" if kind == "relative" else str(tmp_path / "missing" if kind == "missing" else directory)
    response = await client.post("/v1/message", json={
        "text": "run", "agent_id": "code-master", "working_directory": path,
    })
    assert response.status_code == 422
    assert context.store.list_tasks() == []
