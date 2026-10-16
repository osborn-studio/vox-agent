"""API surface tests for organization collaboration."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.org_api import create_org_collab_app
from engine.org_models import Organization, Team, TeamMember, User
from engine.org_store import OrgStore
from engine.source_adapters import manual_note_to_work_event
from engine.work_events import WorkEventStore


@pytest.fixture
def api_client():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        app = create_org_collab_app(data_dir=data_dir)

        org_store = OrgStore(data_dir)
        event_store = WorkEventStore(data_dir)
        org_store.connect()
        event_store.connect()

        org = Organization(name="Vox Agent")
        team = Team(org_id=org.id, name="Coding")
        alice = User(org_id=org.id, email="alice@example.com", display_name="Alice")
        bob = User(org_id=org.id, email="bob@example.com", display_name="Bob")
        outsider = User(
            org_id=org.id,
            email="outsider@example.com",
            display_name="Outsider",
        )

        org_store.insert_organization(org)
        org_store.insert_team(team)
        for user in [alice, bob, outsider]:
            org_store.insert_user(user)
        org_store.insert_team_member(TeamMember(team_id=team.id, user_id=alice.id))
        org_store.insert_team_member(TeamMember(team_id=team.id, user_id=bob.id))

        public_event = manual_note_to_work_event(
            org_id=org.id,
            actor_user_id=alice.id,
            note="launch update: permissioned API surface shipped",
            team_id=team.id,
            project_id="project_api",
        )
        private_event = manual_note_to_work_event(
            org_id=org.id,
            actor_user_id=alice.id,
            note="private launch secret: do not disclose",
            team_id=team.id,
            project_id="project_api",
            private=True,
        )
        public_event.created_at = "2026-05-17T09:00:00+00:00"
        private_event.created_at = "2026-05-17T10:00:00+00:00"
        event_store.insert_work_event(public_event)
        event_store.insert_work_event(private_event)

        org_store.close()
        event_store.close()

        with TestClient(app) as client:
            yield {
                "client": client,
                "data_dir": data_dir,
                "org": org,
                "team": team,
                "alice": alice,
                "bob": bob,
                "outsider": outsider,
                "public_event": public_event,
                "private_event": private_event,
            }


def test_post_work_event_requires_boundary_fields(api_client):
    client = api_client["client"]

    for payload in [
        {
            "actor_user_id": "user_1",
            "requester_user_id": "user_1",
            "source_type": "manual",
            "body": "hello",
        },
        {
            "org_id": "org_1",
            "requester_user_id": "user_1",
            "source_type": "manual",
            "body": "hello",
        },
        {
            "org_id": "org_1",
            "actor_user_id": "user_1",
            "source_type": "manual",
            "body": "hello",
        },
        {
            "org_id": "org_1",
            "actor_user_id": "user_1",
            "requester_user_id": "user_1",
            "body": "hello",
        },
    ]:
        response = client.post("/org/work-events", json=payload)

        assert response.status_code == 422


def test_post_work_event_persists_with_request_context_policy(api_client):
    client = api_client["client"]
    org = api_client["org"]
    team = api_client["team"]
    bob = api_client["bob"]

    response = client.post(
        "/org/work-events",
        json={
            "org_id": org.id,
            "team_id": team.id,
            "project_id": "project_api",
            "actor_user_id": bob.id,
            "requester_user_id": bob.id,
            "source_type": "manual",
            "body": "completed api endpoint contract",
            "metadata": {"status": "completed"},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"]
    assert body["source_id"] == body["id"]
    assert body["visibility"] == "team_raw"


def test_query_returns_cited_answer_without_private_event_leak(api_client):
    client = api_client["client"]
    org = api_client["org"]
    team = api_client["team"]
    bob = api_client["bob"]
    public_event = api_client["public_event"]

    response = client.post(
        "/org/query",
        json={
            "context": {
                "org_id": org.id,
                "requester_user_id": bob.id,
                "team_id": team.id,
                "project_id": "project_api",
                "purpose": "status",
            },
            "question": "launch",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "permissioned API surface shipped" in body["answer"]
    assert "private launch secret" not in body["answer"]
    assert body["source_ids"] == [public_event.id]
    assert body["citations"] == [{"source_id": public_event.id}]
    assert len(body["audit_ids"]) >= 1
    assert body["confidence"] > 0
    assert body["confident"] is True


def test_query_uses_permissioned_retriever_for_team_membership(api_client):
    client = api_client["client"]
    org = api_client["org"]
    team = api_client["team"]
    outsider = api_client["outsider"]

    response = client.post(
        "/org/query",
        json={
            "context": {
                "org_id": org.id,
                "requester_user_id": outsider.id,
                "team_id": team.id,
                "project_id": "project_api",
                "purpose": "status",
            },
            "question": "launch",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_ids"] == []
    assert "private launch secret" not in body["answer"]
    assert body["confident"] is False


def test_dashboard_returns_policy_filtered_team_and_project_summary(api_client):
    client = api_client["client"]
    org = api_client["org"]
    team = api_client["team"]
    bob = api_client["bob"]
    public_event = api_client["public_event"]

    response = client.post(
        "/org/dashboard",
        json={
            "context": {
                "org_id": org.id,
                "requester_user_id": bob.id,
                "team_id": team.id,
                "project_id": "project_api",
                "purpose": "dashboard",
            },
            "day": "2026-05-17",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["team_summary"]["source_ids"] == [public_event.id]
    assert body["team_summary"]["source_count"] == 1
    assert body["team_summary"]["visibility"] == "team_summary"
    assert "permissioned API surface shipped" in body["team_summary"]["body"]
    assert "private launch secret" not in body["team_summary"]["body"]
    assert body["project_report"]["source_ids"] == [public_event.id]
    assert body["project_report"]["source_count"] == 1
    assert body["project_report"]["visibility"] == "project_summary"
