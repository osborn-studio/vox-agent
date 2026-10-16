"""Adversarial HTTP boundary tests for P3.3E live conversation drafts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from engine.app_context import create_app_context
from engine.server import create_app


ADMIN_TOKEN = "live-route-admin-token-at-least-24-chars"


def _conversation_client(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        edition="enterprise",
        bootstrap_admin_token=ADMIN_TOKEN,
    )
    host = context.auth_service.create_user(
        org_id="default-org",
        email="live-host@example.com",
        display_name="Live Host",
    )
    _, principal_token = context.auth_service.issue_credential(
        host["id"],
        "conversation-live",
    )
    return context, host, principal_token, TestClient(create_app(context))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_and_claim(client: TestClient, principal_token: str) -> tuple[dict, dict]:
    created = client.post(
        "/v1/conversation-sessions",
        headers=_bearer(principal_token),
        json={
            "mode": "REMOTE_QR",
            "hostLanguage": "en",
            "guestLanguage": "zh-CN",
        },
    )
    assert created.status_code == 201
    claimed = client.post(
        "/public/v1/conversation-sessions/claim",
        json={"inviteToken": created.json()["invite"]["inviteToken"]},
    )
    assert claimed.status_code == 200
    return created.json(), claimed.json()


def _partial_event(
    session_id: str,
    *,
    text: str = "private live draft",
    language: str = "en",
    revision: int | bool = 1,
    stream_id: str = "live-stream-1",
) -> dict:
    return {
        "schema": "voxagent.speech.event",
        "schemaVersion": 1,
        "eventId": f"live-event-{stream_id}-{revision}",
        "type": "transcript.partial",
        "streamId": stream_id,
        "sessionId": session_id,
        "sequence": 2,
        "monotonicMs": 200,
        "emittedAt": "2026-07-26T14:00:00+00:00",
        "payload": {
            "segmentId": "segment-1",
            "revision": revision,
            "text": text,
            "language": {"code": language, "confidence": 0.99},
            "speaker": {"id": "speaker-1", "label": "Speaker"},
            "audioStartMs": 0,
            "audioEndMs": 800,
            "confidence": 0.98,
        },
    }


def _live_headers(token: str) -> dict[str, str]:
    return {"X-VoxAgent-Conversation-Token": token}


def test_live_http_round_trip_is_current_state_with_metadata_only_audit(
    tmp_path,
) -> None:
    context, host, principal_token, client = _conversation_client(tmp_path)
    created, claimed = _create_and_claim(client, principal_token)
    session_id = created["session"]["id"]
    private_text = "draft-text-must-never-enter-audit"

    published = client.post(
        f"/public/v1/conversation-sessions/{session_id}/live",
        headers=_live_headers(created["hostAccessToken"]),
        json={"speechEvent": _partial_event(session_id, text=private_text)},
    )
    snapshot = client.get(
        f"/public/v1/conversation-sessions/{session_id}/live",
        headers=_live_headers(claimed["accessToken"]),
    )
    replay = client.get(
        f"/public/v1/conversation-sessions/{session_id}/turns?afterSequence=0",
        headers=_live_headers(claimed["accessToken"]),
    )

    assert published.status_code == 200
    assert published.json()["participantRole"] == "HOST"
    assert published.json()["sourceLanguage"] == "en"
    assert published.json()["targetLanguage"] == "zh-CN"
    assert snapshot.status_code == 200
    assert snapshot.json()["host"] == published.json()
    assert snapshot.json()["guest"] is None
    assert replay.status_code == 200
    assert replay.json()["turns"] == []
    assert replay.json()["nextSequence"] == 1

    audit = context.voice_audit_log.list_events()
    live_audit = next(
        event for event in audit if event["action"] == "conversation.live.published"
    )
    assert live_audit["actorId"] == "conversation-participant:HOST"
    assert live_audit["targetId"] == session_id
    assert live_audit["metadata"] == {
        "participant_role": "HOST",
        "stream_id": "live-stream-1",
        "segment_id": "segment-1",
        "revision": 1,
        "source_language": "en",
        "target_language": "zh-CN",
    }
    serialized_audit = repr(audit)
    assert private_text not in serialized_audit
    assert created["hostAccessToken"] not in serialized_audit
    assert claimed["accessToken"] not in serialized_audit
    assert host["email"] not in repr(live_audit["metadata"])


def test_live_http_enforces_role_and_session_capabilities(tmp_path) -> None:
    _context, _host, principal_token, client = _conversation_client(tmp_path)
    first, first_guest = _create_and_claim(client, principal_token)
    second, _second_guest = _create_and_claim(client, principal_token)
    first_id = first["session"]["id"]
    second_id = second["session"]["id"]

    guest_impersonation = client.post(
        f"/public/v1/conversation-sessions/{first_id}/live",
        headers=_live_headers(first_guest["accessToken"]),
        json={"speechEvent": _partial_event(first_id, language="en")},
    )
    cross_session = client.post(
        f"/public/v1/conversation-sessions/{second_id}/live",
        headers=_live_headers(first_guest["accessToken"]),
        json={
            "speechEvent": _partial_event(
                second_id,
                language="zh-CN",
                stream_id="guest-stream",
            )
        },
    )
    missing_token = client.get(
        f"/public/v1/conversation-sessions/{first_id}/live"
    )

    assert guest_impersonation.status_code == 400
    assert cross_session.status_code == 401
    assert missing_token.status_code == 401


def test_live_http_rejects_coercion_hidden_fields_and_non_partial_events(
    tmp_path,
) -> None:
    _context, _host, principal_token, client = _conversation_client(tmp_path)
    created, _claimed = _create_and_claim(client, principal_token)
    session_id = created["session"]["id"]
    url = f"/public/v1/conversation-sessions/{session_id}/live"
    headers = _live_headers(created["hostAccessToken"])

    boolean_revision = client.post(
        url,
        headers=headers,
        json={"speechEvent": _partial_event(session_id, revision=True)},
    )
    hidden_event = _partial_event(session_id)
    hidden_event["hidden"] = "not allowed"
    hidden = client.post(
        url,
        headers=headers,
        json={"speechEvent": hidden_event},
    )
    final_event = _partial_event(session_id)
    final_event["type"] = "transcript.final"
    final = client.post(
        url,
        headers=headers,
        json={"speechEvent": final_event},
    )
    oversized = client.post(
        url,
        headers=headers,
        json={
            "speechEvent": _partial_event(
                session_id,
                text="x" * 32_001,
            )
        },
    )

    assert boolean_revision.status_code == 422
    assert hidden.status_code == 422
    assert final.status_code == 422
    assert oversized.status_code == 422


def test_live_http_expired_and_unknown_sessions_are_uniformly_absent(
    tmp_path,
) -> None:
    context, _host, principal_token, client = _conversation_client(tmp_path)
    created, claimed = _create_and_claim(client, principal_token)
    session_id = created["session"]["id"]
    context.conversation_session_broker._sessions[session_id].expires_clock = -1

    expired = client.get(
        f"/public/v1/conversation-sessions/{session_id}/live",
        headers=_live_headers(claimed["accessToken"]),
    )
    unknown = client.get(
        "/public/v1/conversation-sessions/conv_unknown/live",
        headers=_live_headers(claimed["accessToken"]),
    )

    assert expired.status_code == unknown.status_code == 404
    assert expired.json() == unknown.json()
