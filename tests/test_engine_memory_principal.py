"""Mobile memory and Agent context are isolated by the verified principal."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.app_context import create_app_context
from engine.server import create_app


ADMIN_TOKEN = "memory-principal-admin-token-at-least-24-chars"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _enterprise_client(tmp_path, *, voice_memory_db=None):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=voice_memory_db,
        use_terminal_bridge=False,
        edition="enterprise",
        bootstrap_admin_token=ADMIN_TOKEN,
    )
    alice = context.auth_service.create_user(
        org_id="default-org",
        email="alice-memory@example.com",
        display_name="Alice Memory",
    )
    bob = context.auth_service.create_user(
        org_id="default-org",
        email="bob-memory@example.com",
        display_name="Bob Memory",
    )
    _, alice_token = context.auth_service.issue_credential(alice["id"], "mobile")
    _, bob_token = context.auth_service.issue_credential(bob["id"], "mobile")
    return context, alice, alice_token, bob, bob_token, TestClient(
        create_app(context)
    )


def _node(
    context,
    *,
    workspace_id: str,
    owner_user_id: str,
    title: str,
    source_session_id: str | None = None,
) -> dict:
    return context.voice_memory_repo.add_memory_node(
        {
            "workspaceId": workspace_id,
            "ownerUserId": owner_user_id,
            "sourceSessionId": source_session_id,
            "type": "ACTION_ITEM",
            "title": title,
            "body": f"{title} private body",
            "tags": ["principal-scope"],
            "metadata": {"status": "OPEN"},
            "confidence": 0.9,
        }
    )


@pytest.mark.parametrize("voice_db_name", [None, "voice-memory.db"])
def test_enterprise_member_reads_only_owned_memory_and_agent_context(
    tmp_path,
    voice_db_name,
):
    voice_memory_db = tmp_path / voice_db_name if voice_db_name else None
    context, alice, alice_token, bob, _bob_token, client = _enterprise_client(
        tmp_path,
        voice_memory_db=voice_memory_db,
    )
    own = _node(
        context,
        workspace_id=alice["org_id"],
        owner_user_id=alice["id"],
        title="Alice launch plan",
    )
    same_org_foreign = _node(
        context,
        workspace_id=bob["org_id"],
        owner_user_id=bob["id"],
        title="Bob acquisition secret",
    )
    foreign_org = _node(
        context,
        workspace_id="foreign-org",
        owner_user_id="foreign-user",
        title="Foreign pricing secret",
    )

    with client:
        capture = client.post(
            "/v1/capture/sessions",
            headers=_bearer(alice_token),
            json={
                "source": "MOBILE",
                "mode": "MEETING",
                "privacyLevel": "PERSONAL",
                "consentState": "UNKNOWN",
            },
        ).json()
        _node(
            context,
            workspace_id=bob["org_id"],
            owner_user_id=bob["id"],
            title="Foreign node attached to Alice capture",
            source_session_id=capture["id"],
        )
        missing = client.get("/v1/memory/recent")
        recent = client.get(
            "/v1/memory/recent",
            headers=_bearer(alice_token),
        )
        search = client.get(
            "/v1/memory/search",
            params={"q": "plan secret"},
            headers=_bearer(alice_token),
        )
        action_items = client.get(
            "/v1/memory/action-items",
            headers=_bearer(alice_token),
        )
        own_node = client.get(
            f"/v1/memory/nodes/{own['id']}",
            headers=_bearer(alice_token),
        )
        hidden_nodes = [
            client.get(
                f"/v1/memory/nodes/{node_id}",
                headers=_bearer(alice_token),
            )
            for node_id in (same_org_foreign["id"], foreign_org["id"])
        ]
        automatic_answer = client.post(
            "/v1/agents/query",
            headers=_bearer(alice_token),
            json={"query": "plan secret"},
        )
        explicit_foreign = client.post(
            "/v1/agents/query",
            headers=_bearer(alice_token),
            json={"query": "secret", "nodeIds": [same_org_foreign["id"]]},
        )
        recent_context = client.get(
            "/v1/agents/context/recent",
            headers=_bearer(alice_token),
        )
        capture_summary = client.get(
            f"/v1/capture/sessions/{capture['id']}/summary",
            headers=_bearer(alice_token),
        )
        forbidden_write = client.post(
            "/v1/memory/nodes",
            headers=_bearer(alice_token),
            json={
                "workspaceId": alice["org_id"],
                "ownerUserId": alice["id"],
                "type": "DECISION",
                "title": "member write",
                "body": "must stay admin-only",
            },
        )
        query_events = [
            event
            for event in context.voice_audit_log.list_events()
            if event["action"] == "agent.query"
        ]

    assert missing.status_code == 401
    assert [node["id"] for node in recent.json()] == [own["id"]]
    assert [result["node"]["id"] for result in search.json()["results"]] == [
        own["id"]
    ]
    assert [node["id"] for node in action_items.json()] == [own["id"]]
    assert own_node.status_code == 200
    assert {response.status_code for response in hidden_nodes} == {404}
    assert {response.json()["detail"] for response in hidden_nodes} == {
        "memory node not found"
    }
    assert automatic_answer.status_code == 200
    assert automatic_answer.json()["contextNodeIds"] == [own["id"]]
    assert same_org_foreign["id"] not in str(automatic_answer.json())
    assert foreign_org["id"] not in str(automatic_answer.json())
    assert explicit_foreign.status_code == 404
    assert explicit_foreign.json()["detail"] == "memory node not found"
    assert [node["id"] for node in recent_context.json()["nodes"]] == [own["id"]]
    assert capture_summary.status_code == 200
    assert capture_summary.json()["summaryNodes"] == []
    assert capture_summary.json()["actionItems"] == []
    assert capture_summary.json()["decisions"] == []
    assert forbidden_write.status_code == 403
    assert forbidden_write.json()["detail"]["code"] == "ADMIN_REQUIRED"

    assert query_events[0]["actorId"] == alice["id"]
    assert query_events[0]["workspaceId"] == alice["org_id"]


def test_personal_memory_keeps_unscoped_legacy_nodes_visible(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        edition="personal",
        bootstrap_admin_token=None,
    )
    legacy = context.voice_memory_repo.add_memory_node(
        {
            "workspaceId": None,
            "ownerUserId": None,
            "sourceSessionId": None,
            "type": "DECISION",
            "title": "Legacy VoxLog decision",
            "body": "Keep the local single-user history",
            "tags": ["legacy"],
            "confidence": 0.9,
        }
    )

    with TestClient(create_app(context)) as client:
        recent = client.get("/v1/memory/recent")
        answer = client.post(
            "/v1/agents/query",
            json={"query": "legacy voxlog"},
        )

    assert [node["id"] for node in recent.json()] == [legacy["id"]]
    assert answer.json()["contextNodeIds"] == [legacy["id"]]
