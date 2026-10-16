"""Production org routes bind every tenant decision to the authenticated principal."""

from __future__ import annotations

from fastapi.testclient import TestClient

from engine.app_context import create_app_context
from engine.org_models import Organization
from engine.server import create_app
from engine.source_adapters import manual_note_to_work_event


BOOTSTRAP_TOKEN = "bootstrap-token-for-org-route-tests"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _org_client(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        edition="enterprise",
        bootstrap_admin_token=BOOTSTRAP_TOKEN,
    )
    member = context.auth_service.create_user(
        org_id="default-org",
        email="member@example.com",
        display_name="Member",
    )
    _, member_token = context.auth_service.issue_credential(member["id"], "member")
    return context, member, member_token, TestClient(create_app(context))


def test_org_routes_require_a_valid_bearer_principal(tmp_path):
    context, _member, _member_token, client = _org_client(tmp_path)
    with client:
        missing = client.post("/v1/org/query", json={"question": "status"})
        invalid = client.post(
            "/v1/org/query",
            headers=_bearer("not-a-valid-token"),
            json={"question": "status"},
        )

    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "AUTH_REQUIRED"
    assert invalid.status_code == 401
    assert invalid.json()["detail"]["code"] == "INVALID_CREDENTIAL"
    assert context.store._tasks_db is None
    assert context.org_event_store._db is None


def test_member_creates_event_as_self_and_body_identity_cannot_override(tmp_path):
    context, member, member_token, client = _org_client(tmp_path)
    with client:
        response = client.post(
            "/v1/org/work-events",
            headers=_bearer(member_token),
            json={
                "orgId": "attacker-controlled-org",
                "requesterUserId": "attacker-controlled-user",
                "actorUserId": "attacker-controlled-actor",
                "sourceType": "manual",
                "body": "launch boundary is principal bound",
                "metadata": {"status": "completed"},
            },
        )

        assert response.status_code == 201
        body = response.json()
        event = context.org_event_store.get_work_event(body["id"])

    assert body["orgId"] == "default-org"
    assert body["actorUserId"] == member["id"]
    assert event["org_id"] == "default-org"
    assert event["actor_id"] == member["id"]
    assert event["metadata"] == {"status": "completed"}


def test_query_and_audit_are_scoped_to_principal_org(tmp_path):
    context, member, member_token, client = _org_client(tmp_path)
    other_org = Organization(id="other-org", name="Other Organization")
    context.org_store.insert_organization(other_org)
    leaked = manual_note_to_work_event(
        org_id=other_org.id,
        actor_user_id="other-user",
        note="cross tenant launch secret",
    )
    context.org_event_store.insert_work_event(leaked)

    with client:
        response = client.post(
            "/v1/org/query",
            headers=_bearer(member_token),
            json={
                "orgId": other_org.id,
                "requesterUserId": "other-user",
                "question": "launch",
            },
        )

        assert response.status_code == 200
        body = response.json()
        audits = context.org_access_audit_store.list_audit_records("default-org")

    assert body["sourceIds"] == []
    assert "cross tenant launch secret" not in body["answer"]
    assert all(record["org_id"] == "default-org" for record in audits)
    assert all(record["requester_user_id"] == member["id"] for record in audits)


def test_personal_edition_uses_local_principal_without_bearer(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        edition="personal",
        bootstrap_admin_token=None,
    )
    with TestClient(create_app(context)) as client:
        response = client.post(
            "/v1/org/work-events",
            json={"sourceType": "manual", "body": "personal local event"},
        )

    assert response.status_code == 201
    assert response.json()["orgId"] == "local-org"
    assert response.json()["actorUserId"] == "local-admin"
