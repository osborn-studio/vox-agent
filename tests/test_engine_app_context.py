"""Application composition and route-dependency isolation tests."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from engine.api.admin import router as admin_router
from engine.app_context import create_app_context
from engine.models import Task
from engine.server import create_app


def _admin_app(context):
    app = FastAPI()
    app.state.context = context
    app.include_router(admin_router)
    return app


def test_context_factory_isolates_stores_and_runtime_policies(tmp_path):
    first = create_app_context(data_dir=tmp_path / "first", voice_memory_db=None)
    second = create_app_context(data_dir=tmp_path / "second", voice_memory_db=None)

    assert first.store is not second.store
    assert first.store.data_dir != second.store.data_dir
    assert first.skill_loader.skills_dir == tmp_path / "first" / "skills"
    assert first.agent_memory.memory_dir == tmp_path / "first" / "memory"
    assert second.skill_loader.skills_dir == tmp_path / "second" / "skills"
    assert second.agent_memory.memory_dir == tmp_path / "second" / "memory"
    assert first.scheduler.store is first.store
    assert second.scheduler.store is second.store
    assert first.api.task_analytics.store is first.store
    assert second.api.task_analytics.store is second.store
    assert first.api is not second.api

    first.model_policy.answerer = "llm"
    first.retention_policy.audio_retention_days = 7

    assert second.model_policy.answerer is None
    assert second.retention_policy.audio_retention_days is None


def test_admin_router_reads_the_context_owned_by_each_app(tmp_path):
    first = create_app_context(data_dir=tmp_path / "first", voice_memory_db=None)
    second = create_app_context(data_dir=tmp_path / "second", voice_memory_db=None)
    first_client = TestClient(_admin_app(first))
    second_client = TestClient(_admin_app(second))

    response = first_client.patch(
        "/v1/admin/retention-policy",
        json={"audioRetentionDays": 7},
    )

    assert response.status_code == 200
    assert first_client.get("/v1/admin/retention-policy").json()["audioRetentionDays"] == 7
    assert second_client.get("/v1/admin/retention-policy").json()["audioRetentionDays"] == 30


def test_admin_router_keeps_boolean_day_validation(tmp_path):
    context = create_app_context(data_dir=tmp_path, voice_memory_db=None)
    client = TestClient(_admin_app(context))

    response = client.patch(
        "/v1/admin/retention-policy",
        json={"audioRetentionDays": True},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_app_factory_keeps_route_storage_isolated(tmp_path):
    first = create_app_context(data_dir=tmp_path / "first", voice_memory_db=None)
    second = create_app_context(data_dir=tmp_path / "second", voice_memory_db=None)
    first.store.connect()
    second.store.connect()
    first.store.insert_task(Task(agent_id="claude", title="first only"))

    first_client = AsyncClient(
        transport=ASGITransport(app=create_app(first)), base_url="http://test"
    )
    second_client = AsyncClient(
        transport=ASGITransport(app=create_app(second)), base_url="http://test"
    )

    first_response = await first_client.get("/v1/tasks")
    second_response = await second_client.get("/v1/tasks")
    assert [task["title"] for task in first_response.json()] == ["first only"]
    assert second_response.json() == []
    await first_client.aclose()
    await second_client.aclose()
    first.close()
    second.close()


def test_server_is_a_small_composition_root_without_service_aliases():
    import engine.server as server

    forbidden = {
        "store",
        "scheduler",
        "event_bus",
        "skill_loader",
        "voice_memory_repo",
        "task_analytics",
        "rebind_store",
    }
    assert forbidden.isdisjoint(vars(server))

    source_lines = Path(server.__file__).read_text().splitlines()
    assert len(source_lines) < 200


def test_openapi_surface_preserves_all_http_operations(tmp_path):
    context = create_app_context(data_dir=tmp_path, voice_memory_db=None)
    spec = create_app(context).openapi()
    operations = {
        (method.upper(), path)
        for path, path_item in spec["paths"].items()
        for method in path_item
        if method in {"get", "post", "put", "patch", "delete"}
    }

    assert len(operations) == 162
    assert {
        ("POST", "/v1/tasks"),
        ("POST", "/v1/tasks/{task_id}/pause"),
        ("POST", "/v1/tasks/{task_id}/resume"),
        ("GET", "/v1/permissions/pending"),
        ("POST", "/v1/agents/query"),
        ("POST", "/v1/capture/sessions/{session_id}/process"),
        ("PATCH", "/v1/admin/retention-policy"),
        ("POST", "/v1/org/work-events"),
        ("POST", "/v1/org/query"),
        ("POST", "/v1/org/dashboard"),
        ("POST", "/v1/speech/streams"),
        ("GET", "/v1/speech/streams/{stream_id}/events"),
        ("POST", "/v1/speech/streams/{stream_id}/audio/chunks"),
        ("POST", "/v1/conversation-sessions"),
        ("POST", "/v1/conversation-sessions/{session_id}/end"),
        ("POST", "/public/v1/conversation-sessions/claim"),
        ("GET", "/public/v1/conversation-sessions/{session_id}/live"),
        ("POST", "/public/v1/conversation-sessions/{session_id}/live"),
        ("GET", "/public/v1/conversation-sessions/{session_id}/turns"),
        ("POST", "/public/v1/conversation-sessions/{session_id}/turns"),
        ("POST", "/v1/webhooks/{webhook_id}/receive"),
    } <= operations
    context.close()


def test_app_lifespan_starts_and_closes_context_services(tmp_path):
    context = create_app_context(data_dir=tmp_path, voice_memory_db=None)

    with TestClient(create_app(context)) as client:
        assert client.get("/health").status_code == 200
        assert context.store._tasks_db is not None

    assert context.store._tasks_db is None
    assert context.store._activity_db is None
    assert context.org_store._db is None
    assert context.org_event_store._db is None
    assert context.org_access_audit_store._db is None
