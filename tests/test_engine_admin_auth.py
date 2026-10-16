"""Enterprise admin authentication, authorization, and durable audit tests."""

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.api.admin import router as admin_router
from engine.app_context import create_app_context

ADMIN_TOKEN = "admin-token-with-at-least-24-characters"


def _app(context):
    app = FastAPI()
    app.state.context = context
    app.include_router(admin_router)
    return app


def _headers(token=ADMIN_TOKEN):
    return {"Authorization": f"Bearer {token}"}


def _enterprise_context(data_dir, token=ADMIN_TOKEN):
    return create_app_context(
        data_dir=data_dir,
        voice_memory_db=None,
        edition="enterprise",
        bootstrap_admin_token=token,
    )


def test_enterprise_admin_requires_a_valid_bearer_token(tmp_path):
    context = _enterprise_context(tmp_path)
    client = TestClient(_app(context))

    missing = client.get("/v1/admin/model-policy")
    invalid = client.get("/v1/admin/model-policy", headers=_headers("x" * 32))
    allowed = client.get("/v1/admin/model-policy", headers=_headers())

    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "AUTH_REQUIRED"
    assert missing.headers["www-authenticate"] == "Bearer"
    assert invalid.status_code == 401
    assert invalid.json()["detail"]["code"] == "INVALID_CREDENTIAL"
    assert allowed.status_code == 200


def test_first_enterprise_startup_fails_closed_without_bootstrap_credential(tmp_path):
    with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_TOKEN"):
        create_app_context(
            data_dir=tmp_path,
            voice_memory_db=None,
            edition="enterprise",
            bootstrap_admin_token=None,
        )


def test_enterprise_database_never_stores_the_plaintext_token(tmp_path):
    context = _enterprise_context(tmp_path)
    context.close()

    database_bytes = (tmp_path / "org.db").read_bytes()
    with sqlite3.connect(tmp_path / "org.db") as connection:
        credential = connection.execute(
            "SELECT salt, secret_hash FROM api_credential"
        ).fetchone()

    assert ADMIN_TOKEN.encode() not in database_bytes
    assert credential is not None
    assert ADMIN_TOKEN not in credential


def test_valid_member_credential_is_forbidden_and_audited(tmp_path):
    context = _enterprise_context(tmp_path)
    member = context.auth_service.create_user(
        org_id="default-org",
        email="member@example.com",
        display_name="Member",
        role="member",
    )
    _, member_token = context.auth_service.issue_credential(member["id"], "test")
    client = TestClient(_app(context))

    response = client.get("/v1/admin/retention-policy", headers=_headers(member_token))

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ADMIN_REQUIRED"
    events = context.voice_audit_log.list_events()
    assert events[0]["action"] == "admin.authorization.denied"
    assert events[0]["actorId"] == member["id"]


def test_policy_change_records_actor_and_survives_restart(tmp_path):
    context = _enterprise_context(tmp_path)
    client = TestClient(_app(context))

    response = client.patch(
        "/v1/admin/retention-policy",
        headers=_headers(),
        json={"audioRetentionDays": 14},
    )
    actor_id = context.auth_service.authenticate(ADMIN_TOKEN).user_id
    context.close()

    restarted = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        edition="enterprise",
        bootstrap_admin_token=None,
    )
    events = restarted.voice_audit_log.list_events()

    assert response.status_code == 200
    assert events[0]["action"] == "admin.retention-policy.updated"
    assert events[0]["actorId"] == actor_id
    assert events[0]["metadata"]["changes"] == {"audioRetentionDays": 14}
    assert restarted.retention_policy.effective()["audioRetentionDays"] == 14
    assert restarted.auth_service.authenticate(ADMIN_TOKEN).user_id == actor_id


def test_admin_can_preview_and_execute_retention_sweep(tmp_path):
    context = _enterprise_context(tmp_path)
    client = TestClient(_app(context))

    preview = client.post(
        "/v1/admin/retention/sweeps",
        headers=_headers(),
        json={"dryRun": True},
    )
    latest = client.get(
        "/v1/admin/retention/sweeps/latest",
        headers=_headers(),
    )
    executed = client.post(
        "/v1/admin/retention/sweeps",
        headers=_headers(),
        json={"dryRun": False},
    )

    assert preview.status_code == 200
    assert preview.json()["dryRun"] is True
    assert latest.json()["id"] == preview.json()["id"]
    assert executed.status_code == 200
    assert executed.json()["dryRun"] is False
    assert context.voice_audit_log.list_events()[0]["action"] == (
        "admin.retention-sweep.executed"
    )


def test_admin_can_create_user_update_role_and_issue_then_revoke_credential(tmp_path):
    context = _enterprise_context(tmp_path)
    client = TestClient(_app(context))

    created = client.post(
        "/v1/admin/users",
        headers=_headers(),
        json={
            "email": "lead@example.com",
            "displayName": "Lead",
            "role": "tech_lead",
        },
    )
    user_id = created.json()["id"]
    updated = client.patch(
        f"/v1/admin/users/{user_id}",
        headers=_headers(),
        json={"role": "admin"},
    )
    credential = client.post(
        f"/v1/admin/users/{user_id}/credentials",
        headers=_headers(),
        json={"name": "CLI"},
    )
    credential_body = credential.json()
    listed = client.get("/v1/admin/users", headers=_headers())
    revoked = client.delete(
        f"/v1/admin/credentials/{credential_body['id']}",
        headers=_headers(),
    )

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["role"] == "admin"
    assert credential.status_code == 201
    assert len(credential_body["token"]) >= 32
    assert any(user["id"] == user_id for user in listed.json())
    assert revoked.status_code == 204
    assert context.auth_service.authenticate(credential_body["token"]) is None
