"""Enterprise control-plane routes fail closed and preserve actor identity."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from engine import __version__
from engine.app_context import create_app_context
from engine.permissions import PermissionRequest
from engine.server import create_app


ADMIN_TOKEN = "control-plane-admin-token-at-least-24-chars"


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
    member = context.auth_service.create_user(
        org_id="default-org",
        email="member-control@example.com",
        display_name="Member Control",
    )
    _, member_token = context.auth_service.issue_credential(member["id"], "member")
    return context, member, member_token, TestClient(create_app(context))


def test_health_is_public_but_legacy_control_plane_requires_admin(tmp_path):
    _context, _member, member_token, client = _enterprise_client(tmp_path)

    with client:
        health = client.get("/health")
        missing = client.get("/v1/engine/status")
        member = client.get("/v1/engine/status", headers=_bearer(member_token))
        admin = client.get("/v1/engine/status", headers=_bearer(ADMIN_TOKEN))

    assert health.status_code == 200
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "AUTH_REQUIRED"
    assert member.status_code == 403
    assert member.json()["detail"]["code"] == "ADMIN_REQUIRED"
    assert admin.status_code == 200


def test_openapi_presents_the_vox_agent_product_name(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        edition="personal",
        bootstrap_admin_token=None,
    )

    with TestClient(create_app(context)) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Vox Agent Engine"
    # One product version (tests/test_release_version.py pins its value).
    assert response.json()["info"]["version"] == __version__


def test_engine_event_websocket_requires_an_admin_principal(tmp_path):
    _context, _member, member_token, client = _enterprise_client(tmp_path)

    with client:
        with pytest.raises(WebSocketDisconnect) as missing:
            with client.websocket_connect("/ws"):
                pass
        with pytest.raises(WebSocketDisconnect) as member:
            with client.websocket_connect("/ws", headers=_bearer(member_token)):
                pass
        with client.websocket_connect("/ws", headers=_bearer(ADMIN_TOKEN)):
            pass

    assert missing.value.code == 1008
    assert member.value.code == 1008


def test_permission_decision_accepts_request_owner_and_records_verified_actor(tmp_path):
    context, member, member_token, client = _enterprise_client(tmp_path)
    resolved = []

    def resolve_for_principal(*args, **kwargs):
        resolved.append((args, kwargs))
        return kwargs["requester_user_id"] == member["id"]

    context.permission_manager.resolve_approval_for_principal = resolve_for_principal

    with client:
        missing = client.post(
            "/v1/permissions/approve",
            json={"approval_id": "approval-1", "approved": True},
        )
        approved = client.post(
            "/v1/permissions/approve",
            headers=_bearer(member_token),
            json={"approval_id": "approval-1", "approved": True},
        )
        coerced = client.post(
            "/v1/permissions/approve",
            headers=_bearer(member_token),
            json={"approval_id": "approval-2", "approved": "true"},
        )
        hidden = client.post(
            "/v1/permissions/approve",
            headers=_bearer(member_token),
            json={
                "approval_id": "approval-3",
                "approved": False,
                "unexpected": "value",
            },
        )
    with sqlite3.connect(tmp_path / "activity.db") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """SELECT actor_id, action, details FROM activity_log
               WHERE action = 'permission:approved'
               ORDER BY created_at DESC"""
        ).fetchone()

    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "AUTH_REQUIRED"
    assert approved.status_code == 200
    assert coerced.status_code == 422
    assert hidden.status_code == 422
    assert len(resolved) == 1
    assert resolved[0][1]["requester_user_id"] == member["id"]
    assert resolved[0][1]["workspace_id"] == "default-org"
    assert row["actor_id"] == member["id"]
    assert row["action"] == "permission:approved"
    assert json.loads(row["details"])["workspace_id"] == "default-org"


def test_pending_permission_snapshot_is_principal_scoped(tmp_path):
    context, member, member_token, client = _enterprise_client(tmp_path)
    admin = context.auth_service.authenticate(ADMIN_TOKEN)
    member_request = PermissionRequest(
        approval_id="approval-member",
        task_id="task-member",
        agent_id="codex",
        tool="write",
        scope={"file_path": "/tmp/member.txt"},
        requested_at="2026-07-19T00:00:00+00:00",
        expires_at="2026-07-19T00:02:00+00:00",
    )
    admin_request = PermissionRequest(
        approval_id="approval-admin",
        task_id="task-admin",
        agent_id="codex",
        tool="edit",
        scope={"file_path": "/tmp/admin.txt"},
        requested_at="2026-07-19T00:01:00+00:00",
        expires_at="2026-07-19T00:03:00+00:00",
    )
    context.permission_manager._pending_requests.update({
        member_request.approval_id: member_request,
        admin_request.approval_id: admin_request,
    })
    context.permission_manager._pending_principals.update({
        member_request.approval_id: (member["id"], "default-org"),
        admin_request.approval_id: (admin.user_id, admin.org_id),
    })

    with client:
        missing = client.get("/v1/permissions/pending")
        member_response = client.get(
            "/v1/permissions/pending",
            headers=_bearer(member_token),
        )
        admin_response = client.get(
            "/v1/permissions/pending",
            headers=_bearer(ADMIN_TOKEN),
        )

    assert missing.status_code == 401
    assert member_response.status_code == 200
    assert admin_response.status_code == 200
    assert member_response.json() == [
        {
            "approval_id": "approval-member",
            "task_id": "task-member",
            "agent_id": "codex",
            "tool": "write",
            "scope": {"file_path": "/tmp/member.txt"},
            "requested_at": "2026-07-19T00:00:00+00:00",
            "expires_at": "2026-07-19T00:02:00+00:00",
        }
    ]
    assert [item["approval_id"] for item in admin_response.json()] == ["approval-admin"]


def test_created_task_persists_the_verified_requester_principal(tmp_path):
    context, _member, _member_token, client = _enterprise_client(tmp_path)
    admin = context.auth_service.authenticate(ADMIN_TOKEN)

    with client:
        response = client.post(
            "/v1/tasks",
            headers=_bearer(ADMIN_TOKEN),
            json={
                "task_id": "principal-owned-task",
                "agent_id": "codex",
                "title": "Principal ownership",
                "prompt": "inspect ownership",
                "provider": "noop",
            },
        )

    assert response.status_code == 200
    with sqlite3.connect(tmp_path / "tasks.db") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT requester_user_id, workspace_id FROM task WHERE id = ?",
            ("principal-owned-task",),
        ).fetchone()
    assert row["requester_user_id"] == admin.user_id
    assert row["workspace_id"] == admin.org_id


def test_personal_control_plane_remains_local_without_bearer(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        edition="personal",
        bootstrap_admin_token=None,
    )

    with TestClient(create_app(context)) as client:
        response = client.get("/v1/engine/status")
        with client.websocket_connect("/ws"):
            pass

    assert response.status_code == 200


def test_capture_creation_ignores_spoofed_identity_in_personal_edition(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        edition="personal",
        bootstrap_admin_token=None,
    )

    with TestClient(create_app(context)) as client:
        response = client.post(
            "/v1/capture/sessions",
            json={
                "workspaceId": "spoofed-workspace",
                "ownerUserId": "spoofed-user",
                "source": "MOBILE",
                "mode": "MEETING",
                "privacyLevel": "PERSONAL",
                "consentState": "UNKNOWN",
            },
        )

    assert response.status_code == 200
    assert response.json()["workspaceId"] == "local-org"
    assert response.json()["ownerUserId"] == "local-admin"
    assert response.json()["edition"] == "PERSONAL"


def test_capture_creation_uses_authenticated_enterprise_principal(tmp_path):
    context, _member, _member_token, client = _enterprise_client(tmp_path)

    with client:
        response = client.post(
            "/v1/capture/sessions",
            headers=_bearer(ADMIN_TOKEN),
            json={
                "workspaceId": "spoofed-workspace",
                "ownerUserId": "spoofed-user",
                "edition": "PERSONAL",
                "source": "MOBILE",
                "mode": "MEETING",
                "privacyLevel": "PERSONAL",
                "consentState": "UNKNOWN",
            },
        )

    principal = context.auth_service.authenticate(ADMIN_TOKEN)
    assert response.status_code == 200
    assert response.json()["workspaceId"] == principal.org_id
    assert response.json()["ownerUserId"] == principal.user_id
    assert response.json()["edition"] == "ENTERPRISE"


@pytest.mark.parametrize("voice_db_name", [None, "voice-memory.db"])
def test_enterprise_capture_routes_allow_members_and_hide_foreign_sessions(
    tmp_path,
    voice_db_name,
):
    voice_memory_db = tmp_path / voice_db_name if voice_db_name else None
    context, alice, alice_token, client = _enterprise_client(
        tmp_path,
        voice_memory_db=voice_memory_db,
    )
    bob = context.auth_service.create_user(
        org_id="default-org",
        email="bob-capture@example.com",
        display_name="Bob Capture",
    )
    _, bob_token = context.auth_service.issue_credential(bob["id"], "capture")
    capture_payload = {
        "workspaceId": "spoofed-workspace",
        "ownerUserId": "spoofed-user",
        "source": "MOBILE",
        "mode": "MEETING",
        "privacyLevel": "PERSONAL",
        "consentState": "UNKNOWN",
    }

    with client:
        created = client.post(
            "/v1/capture/sessions",
            headers=_bearer(alice_token),
            json=capture_payload,
        )
        session_id = created.json().get("id", "missing")
        alice_list = client.get(
            "/v1/capture/sessions",
            headers=_bearer(alice_token),
        )
        bob_list = client.get(
            "/v1/capture/sessions",
            headers=_bearer(bob_token),
        )
        external_audio_path = client.post(
            f"/v1/capture/sessions/{session_id}/process",
            headers=_bearer(alice_token),
            json={"audioPath": "/etc/passwd"},
        )
        hidden = [
            client.get(
                f"/v1/capture/sessions/{session_id}",
                headers=_bearer(bob_token),
            ),
            client.patch(
                f"/v1/capture/sessions/{session_id}",
                headers=_bearer(bob_token),
                json={"title": "stolen"},
            ),
            client.post(
                f"/v1/capture/sessions/{session_id}/transcript-segments",
                headers=_bearer(bob_token),
                json={"textRaw": "stolen"},
            ),
            client.get(
                f"/v1/capture/sessions/{session_id}/transcript-segments",
                headers=_bearer(bob_token),
            ),
            client.post(
                f"/v1/capture/sessions/{session_id}/audio",
                headers=_bearer(bob_token),
                files={"file": ("capture.wav", b"not-a-real-wave", "audio/wav")},
            ),
            client.post(
                f"/v1/capture/sessions/{session_id}/process",
                headers=_bearer(bob_token),
                json={},
            ),
            client.get(
                f"/v1/capture/sessions/{session_id}/summary",
                headers=_bearer(bob_token),
            ),
        ]

    assert created.status_code == 200
    assert created.json()["workspaceId"] == alice["org_id"]
    assert created.json()["ownerUserId"] == alice["id"]
    assert [session["id"] for session in alice_list.json()] == [session_id]
    assert bob_list.json() == []
    assert external_audio_path.status_code == 400
    assert external_audio_path.json()["detail"] == (
        "enterprise capture processing requires session-uploaded audio"
    )
    assert {response.status_code for response in hidden} == {404}
    assert {response.json()["detail"] for response in hidden} == {
        "capture session not found"
    }


def test_capture_audio_upload_enforces_configured_size_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXAGENT_MAX_AUDIO_UPLOAD_BYTES", "4")
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        edition="personal",
        bootstrap_admin_token=None,
    )

    with TestClient(create_app(context)) as client:
        session = client.post(
            "/v1/capture/sessions",
            json={
                "source": "MOBILE",
                "mode": "MEETING",
                "privacyLevel": "PERSONAL",
                "consentState": "UNKNOWN",
            },
        ).json()
        response = client.post(
            f"/v1/capture/sessions/{session['id']}/audio",
            files={"file": ("capture.wav", b"12345", "audio/wav")},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "capture audio exceeds the upload limit"
