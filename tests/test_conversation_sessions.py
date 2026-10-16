"""P3.3 ephemeral face-to-face and remote QR conversation protocol."""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from engine.app_context import create_app_context
from engine.conversation_sessions import (
    ConversationCapacityExceeded,
    ConversationNotFound,
    ConversationSessionBroker,
    ConversationTurnCapacityExceeded,
    InvalidConversationToken,
    InvalidConversationTurn,
    InviteUnavailable,
    ReplayGap,
    TurnIdempotencyConflict,
)
from engine.server import create_app


ADMIN_TOKEN = "conversation-admin-token-at-least-24-chars"


class FakeTime:
    def __init__(self) -> None:
        self.seconds = 100.0
        self.wall = datetime(2026, 7, 19, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.seconds += seconds
        self.wall += timedelta(seconds=seconds)


@pytest.fixture
def protocol():
    clock = FakeTime()
    tokens = deque(f"opaque-token-{index:02d}-abcdefghijklmnopqrstuvwxyz" for index in range(30))
    broker = ConversationSessionBroker(
        clock=clock.monotonic,
        utcnow=clock.now,
        token_factory=tokens.popleft,
    )
    return broker, clock


def _create_remote(broker: ConversationSessionBroker) -> dict:
    return broker.create_session(
        owner_user_id="host-user",
        workspace_id="private-org",
        mode="REMOTE_QR",
        host_language="en",
        guest_language="zh-CN",
    )


def _speech_event(session_id: str, *, text: str = "hello", segment_id: str = "seg-1") -> dict:
    return {
        "schema": "voxagent.speech.event",
        "schemaVersion": 1,
        "eventId": "speech-event-1",
        "type": "transcript.final",
        "streamId": "speech-stream-1",
        "sessionId": session_id,
        "sequence": 2,
        "monotonicMs": 200,
        "emittedAt": "2026-07-19T00:00:01+00:00",
        "payload": {
            "segmentId": segment_id,
            "revision": 1,
            "text": text,
            "language": {"code": "en", "confidence": 0.99},
            "speaker": {"id": "speaker-host", "label": "Host"},
            "audioStartMs": 0,
            "audioEndMs": 800,
            "confidence": 0.98,
        },
    }


def _partial_speech_event(
    session_id: str,
    *,
    text: str = "hel",
    segment_id: str = "seg-1",
    revision: int = 1,
    language: str = "en",
    stream_id: str = "speech-stream-1",
) -> dict:
    event = _speech_event(session_id, text=text, segment_id=segment_id)
    event["eventId"] = f"partial-{segment_id}-{revision}"
    event["type"] = "transcript.partial"
    event["streamId"] = stream_id
    event["payload"]["revision"] = revision
    event["payload"]["language"]["code"] = language
    return event


def _translation_event(*, text: str = "你好", segment_id: str = "seg-1") -> dict:
    return {
        "schema": "voxagent.translation.event",
        "schemaVersion": 1,
        "eventId": "translation-event-1",
        "translationId": "translation-1",
        "type": "translation.final",
        "emittedAt": "2026-07-19T00:00:02+00:00",
        "latencyMs": 50,
        "payload": {
            "sourceLanguage": "en",
            "targetLanguage": "zh-CN",
            "sourceSegments": [
                {
                    "streamId": "speech-stream-1",
                    "segmentId": segment_id,
                    "revision": 1,
                    "audioStartMs": 0,
                    "audioEndMs": 800,
                }
            ],
            "text": text,
            "confidence": 0.96,
            "provider": "strict-test",
            "model": "test-v1",
            "glossaryHits": [],
        },
    }


def _claim_remote(broker: ConversationSessionBroker, created: dict) -> dict:
    return broker.claim_invite(created["invite"]["inviteToken"])


def test_remote_qr_contains_only_version_and_opaque_invite(protocol) -> None:
    broker, _clock = protocol

    created = _create_remote(broker)

    assert created["session"]["status"] == "WAITING"
    assert created["session"]["participantCount"] == 1
    assert created["hostAccessToken"].startswith("opaque-token-")
    assert created["localGuestAccessToken"] is None
    assert created["invite"]["qrPayload"].startswith(
        "voxagent://conversation/join#v=1&token="
    )
    rendered = str(created["invite"])
    assert "host-user" not in rendered
    assert "private-org" not in rendered
    assert "backend" not in rendered.lower()


def test_face_to_face_is_active_without_an_invite(protocol) -> None:
    broker, _clock = protocol

    created = broker.create_session(
        owner_user_id="host-user",
        workspace_id="private-org",
        mode="FACE_TO_FACE",
        host_language="en",
        guest_language="ja",
    )

    assert created["session"]["status"] == "ACTIVE"
    assert created["session"]["participantCount"] == 2
    assert created["invite"] is None
    assert created["localGuestAccessToken"].startswith("opaque-token-")


def test_invite_is_single_use_and_raw_tokens_are_not_stored(protocol) -> None:
    broker, _clock = protocol
    created = _create_remote(broker)

    claimed = _claim_remote(broker, created)

    assert claimed["session"]["status"] == "ACTIVE"
    assert claimed["participantRole"] == "GUEST"
    assert claimed["accessToken"].startswith("opaque-token-")
    with pytest.raises(InviteUnavailable):
        _claim_remote(broker, created)
    stored = repr(broker._sessions)
    assert created["invite"]["inviteToken"] not in stored
    assert created["hostAccessToken"] not in stored
    assert claimed["accessToken"] not in stored


def test_concurrent_claim_allows_exactly_one_guest(protocol) -> None:
    broker, _clock = protocol
    created = _create_remote(broker)
    invite = created["invite"]["inviteToken"]

    def claim() -> str:
        try:
            broker.claim_invite(invite)
            return "claimed"
        except InviteUnavailable:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _index: claim(), range(2)))

    assert outcomes == ["claimed", "rejected"]


def test_invite_and_session_expiry_invalidate_every_secret(protocol) -> None:
    broker, clock = protocol
    created = _create_remote(broker)
    session_id = created["session"]["id"]

    clock.advance(301)
    with pytest.raises(InviteUnavailable):
        _claim_remote(broker, created)

    fresh = _create_remote(broker)
    guest = _claim_remote(broker, fresh)
    clock.advance(3601)
    with pytest.raises(ConversationNotFound):
        broker.replay_turns(
            fresh["session"]["id"],
            guest["accessToken"],
            after_sequence=0,
        )
    with pytest.raises(ConversationNotFound):
        broker.replay_turns(session_id, created["hostAccessToken"], after_sequence=0)


def test_published_turn_requires_matching_session_citation_and_language(protocol) -> None:
    broker, _clock = protocol
    created = _create_remote(broker)
    _claim_remote(broker, created)
    session_id = created["session"]["id"]

    turn = broker.publish_turn(
        session_id,
        created["hostAccessToken"],
        client_event_id="host-turn-1",
        speech_event=_speech_event(session_id),
        translation_event=_translation_event(),
    )

    assert turn["sequence"] == 1
    assert turn["participantRole"] == "HOST"
    replay = broker.replay_turns(
        session_id,
        created["hostAccessToken"],
        after_sequence=0,
    )
    assert replay["turns"] == [turn]
    assert replay["nextSequence"] == 2

    wrong_session = _speech_event("conv-other")
    with pytest.raises(InvalidConversationTurn):
        broker.publish_turn(
            session_id,
            created["hostAccessToken"],
            client_event_id="host-turn-2",
            speech_event=wrong_session,
            translation_event=_translation_event(),
        )

    wrong_citation = _translation_event(segment_id="seg-other")
    with pytest.raises(InvalidConversationTurn):
        broker.publish_turn(
            session_id,
            created["hostAccessToken"],
            client_event_id="host-turn-3",
            speech_event=_speech_event(session_id),
            translation_event=wrong_citation,
        )


def test_participant_direction_and_cross_session_tokens_are_enforced(protocol) -> None:
    broker, _clock = protocol
    first = _create_remote(broker)
    first_guest = _claim_remote(broker, first)
    second = _create_remote(broker)

    with pytest.raises(InvalidConversationToken):
        broker.replay_turns(
            second["session"]["id"],
            first_guest["accessToken"],
            after_sequence=0,
        )

    guest_speech = _speech_event(first["session"]["id"])
    with pytest.raises(InvalidConversationTurn):
        broker.publish_turn(
            first["session"]["id"],
            first_guest["accessToken"],
            client_event_id="guest-wrong-direction",
            speech_event=guest_speech,
            translation_event=_translation_event(),
        )


def test_turn_idempotency_returns_same_event_and_rejects_reuse(protocol) -> None:
    broker, _clock = protocol
    created = _create_remote(broker)
    _claim_remote(broker, created)
    session_id = created["session"]["id"]

    first = broker.publish_turn(
        session_id,
        created["hostAccessToken"],
        client_event_id="stable-key",
        speech_event=_speech_event(session_id),
        translation_event=_translation_event(),
    )
    duplicate = broker.publish_turn(
        session_id,
        created["hostAccessToken"],
        client_event_id="stable-key",
        speech_event=_speech_event(session_id),
        translation_event=_translation_event(),
    )

    assert duplicate == first
    with pytest.raises(TurnIdempotencyConflict):
        broker.publish_turn(
            session_id,
            created["hostAccessToken"],
            client_event_id="stable-key",
            speech_event=_speech_event(session_id, text="different"),
            translation_event=_translation_event(text="不同"),
        )


def test_live_draft_is_bounded_current_state_not_final_replay(protocol) -> None:
    broker, _clock = protocol
    created = _create_remote(broker)
    guest = _claim_remote(broker, created)
    session_id = created["session"]["id"]

    draft = broker.publish_live(
        session_id,
        created["hostAccessToken"],
        speech_event=_partial_speech_event(session_id),
    )
    host_snapshot = broker.read_live(session_id, created["hostAccessToken"])
    guest_snapshot = broker.read_live(session_id, guest["accessToken"])
    replay = broker.replay_turns(
        session_id,
        guest["accessToken"],
        after_sequence=0,
    )

    assert draft["schema"] == "voxagent.conversation.live"
    assert draft["schemaVersion"] == 1
    assert draft["type"] == "conversation.live"
    assert draft["sessionId"] == session_id
    assert draft["participantRole"] == "HOST"
    assert draft["streamId"] == "speech-stream-1"
    assert draft["segmentId"] == "seg-1"
    assert draft["revision"] == 1
    assert host_snapshot == guest_snapshot
    assert host_snapshot == {
        "sessionId": session_id,
        "host": draft,
        "guest": None,
        "status": "ACTIVE",
    }
    assert replay["turns"] == []
    assert replay["nextSequence"] == 1
    assert len(broker._sessions[session_id].live_drafts) == 1


def test_live_revision_is_strict_monotonic_and_bool_is_not_an_integer(protocol) -> None:
    broker, _clock = protocol
    created = _create_remote(broker)
    _claim_remote(broker, created)
    session_id = created["session"]["id"]
    first_event = _partial_speech_event(session_id)

    first = broker.publish_live(
        session_id,
        created["hostAccessToken"],
        speech_event=first_event,
    )
    duplicate = broker.publish_live(
        session_id,
        created["hostAccessToken"],
        speech_event=first_event,
    )
    second = broker.publish_live(
        session_id,
        created["hostAccessToken"],
        speech_event=_partial_speech_event(
            session_id,
            text="hello",
            revision=2,
        ),
    )

    assert duplicate == first
    assert second["revision"] == 2
    with pytest.raises(InvalidConversationTurn):
        broker.publish_live(
            session_id,
            created["hostAccessToken"],
            speech_event=_partial_speech_event(
                session_id,
                text="changed at same revision",
                revision=2,
            ),
        )
    with pytest.raises(InvalidConversationTurn):
        broker.publish_live(
            session_id,
            created["hostAccessToken"],
            speech_event=_partial_speech_event(session_id, revision=1),
        )
    with pytest.raises(InvalidConversationTurn):
        broker.publish_live(
            session_id,
            created["hostAccessToken"],
            speech_event=_partial_speech_event(session_id, revision=True),
        )


def test_live_drafts_are_role_scoped_and_require_active_session(protocol) -> None:
    broker, _clock = protocol
    waiting = _create_remote(broker)
    with pytest.raises(InvalidConversationTurn):
        broker.publish_live(
            waiting["session"]["id"],
            waiting["hostAccessToken"],
            speech_event=_partial_speech_event(waiting["session"]["id"]),
        )

    guest = _claim_remote(broker, waiting)
    session_id = waiting["session"]["id"]
    with pytest.raises(InvalidConversationTurn):
        broker.publish_live(
            session_id,
            guest["accessToken"],
            speech_event=_partial_speech_event(session_id),
        )

    guest_draft = broker.publish_live(
        session_id,
        guest["accessToken"],
        speech_event=_partial_speech_event(
            session_id,
            text="你好",
            language="zh-CN",
            stream_id="guest-stream",
        ),
    )
    host_draft = broker.publish_live(
        session_id,
        waiting["hostAccessToken"],
        speech_event=_partial_speech_event(session_id),
    )
    snapshot = broker.read_live(session_id, waiting["hostAccessToken"])

    assert guest_draft["participantRole"] == "GUEST"
    assert host_draft["participantRole"] == "HOST"
    assert snapshot["guest"] == guest_draft
    assert snapshot["host"] == host_draft
    assert len(broker._sessions[session_id].live_drafts) == 2

    other = _create_remote(broker)
    with pytest.raises(InvalidConversationToken):
        broker.read_live(other["session"]["id"], guest["accessToken"])


def test_matching_final_clears_only_its_live_draft(protocol) -> None:
    broker, _clock = protocol
    created = _create_remote(broker)
    _claim_remote(broker, created)
    session_id = created["session"]["id"]

    broker.publish_live(
        session_id,
        created["hostAccessToken"],
        speech_event=_partial_speech_event(session_id, segment_id="seg-1"),
    )
    broker.publish_turn(
        session_id,
        created["hostAccessToken"],
        client_event_id="host-final-1",
        speech_event=_speech_event(session_id, segment_id="seg-1"),
        translation_event=_translation_event(segment_id="seg-1"),
    )
    assert broker.read_live(session_id, created["hostAccessToken"])["host"] is None
    with pytest.raises(InvalidConversationTurn):
        broker.publish_live(
            session_id,
            created["hostAccessToken"],
            speech_event=_partial_speech_event(
                session_id,
                text="late partial",
                segment_id="seg-1",
                revision=2,
            ),
        )

    current = broker.publish_live(
        session_id,
        created["hostAccessToken"],
        speech_event=_partial_speech_event(session_id, segment_id="seg-2"),
    )
    broker.publish_turn(
        session_id,
        created["hostAccessToken"],
        client_event_id="host-final-old",
        speech_event=_speech_event(session_id, segment_id="seg-1"),
        translation_event=_translation_event(segment_id="seg-1"),
    )
    assert broker.read_live(session_id, created["hostAccessToken"])["host"] == current


def test_live_draft_rejects_hidden_or_oversized_content_and_expires(protocol) -> None:
    broker, clock = protocol
    created = _create_remote(broker)
    guest = _claim_remote(broker, created)
    session_id = created["session"]["id"]

    hidden = _partial_speech_event(session_id)
    hidden["payload"]["hidden"] = "not allowed"
    with pytest.raises(InvalidConversationTurn):
        broker.publish_live(
            session_id,
            created["hostAccessToken"],
            speech_event=hidden,
        )
    with pytest.raises(InvalidConversationTurn):
        broker.publish_live(
            session_id,
            created["hostAccessToken"],
            speech_event=_partial_speech_event(session_id, text="x" * 32_001),
        )

    broker.publish_live(
        session_id,
        created["hostAccessToken"],
        speech_event=_partial_speech_event(session_id),
    )
    clock.advance(3_601)
    with pytest.raises(ConversationNotFound):
        broker.read_live(session_id, guest["accessToken"])


def test_bounded_replay_reports_gap(protocol) -> None:
    _broker, clock = protocol
    tokens = deque(f"bounded-token-{index:02d}-abcdefghijklmnopqrstuvwxyz" for index in range(20))
    broker = ConversationSessionBroker(
        max_turns_per_session=2,
        clock=clock.monotonic,
        utcnow=clock.now,
        token_factory=tokens.popleft,
    )
    created = _create_remote(broker)
    _claim_remote(broker, created)
    session_id = created["session"]["id"]

    for index in range(3):
        broker.publish_turn(
            session_id,
            created["hostAccessToken"],
            client_event_id=f"turn-{index}",
            speech_event=_speech_event(session_id, text=f"hello {index}"),
            translation_event=_translation_event(text=f"你好 {index}"),
        )

    with pytest.raises(ReplayGap) as failure:
        broker.replay_turns(
            session_id,
            created["hostAccessToken"],
            after_sequence=0,
        )
    assert failure.value.earliest_sequence == 2
    replay = broker.replay_turns(
        session_id,
        created["hostAccessToken"],
        after_sequence=1,
    )
    assert [turn["sequence"] for turn in replay["turns"]] == [2, 3]


def test_host_end_immediately_invalidates_participants(protocol) -> None:
    broker, _clock = protocol
    created = _create_remote(broker)
    guest = _claim_remote(broker, created)
    session_id = created["session"]["id"]

    ended = broker.end_session(session_id, created["hostAccessToken"])

    assert ended["status"] == "ENDED"
    with pytest.raises(ConversationNotFound):
        broker.replay_turns(session_id, guest["accessToken"], after_sequence=0)


def test_capacity_is_bounded_after_expired_sessions_are_pruned(protocol) -> None:
    _broker, clock = protocol
    tokens = deque(f"capacity-token-{index:02d}-abcdefghijklmnopqrstuvwxyz" for index in range(20))
    broker = ConversationSessionBroker(
        max_sessions=1,
        clock=clock.monotonic,
        utcnow=clock.now,
        token_factory=tokens.popleft,
    )
    _create_remote(broker)
    with pytest.raises(ConversationCapacityExceeded):
        _create_remote(broker)

    clock.advance(3601)
    replacement = _create_remote(broker)
    assert replacement["session"]["status"] == "WAITING"


def test_total_turn_capacity_bounds_valid_token_abuse(protocol) -> None:
    _broker, clock = protocol
    tokens = deque(f"rate-token-{index:02d}-abcdefghijklmnopqrstuvwxyz" for index in range(20))
    broker = ConversationSessionBroker(
        max_turns_per_session=2,
        max_total_turns_per_session=2,
        clock=clock.monotonic,
        utcnow=clock.now,
        token_factory=tokens.popleft,
    )
    created = _create_remote(broker)
    _claim_remote(broker, created)
    session_id = created["session"]["id"]
    for index in range(2):
        broker.publish_turn(
            session_id,
            created["hostAccessToken"],
            client_event_id=f"allowed-{index}",
            speech_event=_speech_event(session_id, text=f"hello {index}"),
            translation_event=_translation_event(text=f"你好 {index}"),
        )

    with pytest.raises(ConversationTurnCapacityExceeded):
        broker.publish_turn(
            session_id,
            created["hostAccessToken"],
            client_event_id="blocked",
            speech_event=_speech_event(session_id, text="blocked"),
            translation_event=_translation_event(text="被阻止"),
        )


def _enterprise_conversation_client(tmp_path):
    context = create_app_context(
        data_dir=tmp_path,
        voice_memory_db=None,
        use_terminal_bridge=False,
        edition="enterprise",
        bootstrap_admin_token=ADMIN_TOKEN,
    )
    host = context.auth_service.create_user(
        org_id="default-org",
        email="conversation-host@example.com",
        display_name="Conversation Host",
    )
    _, host_token = context.auth_service.issue_credential(host["id"], "conversation")
    outsider = context.auth_service.create_user(
        org_id="default-org",
        email="conversation-outsider@example.com",
        display_name="Conversation Outsider",
    )
    _, outsider_token = context.auth_service.issue_credential(
        outsider["id"], "conversation"
    )
    return (
        context,
        host,
        host_token,
        outsider_token,
        TestClient(create_app(context)),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_remote_http(client: TestClient, host_token: str) -> dict:
    response = client.post(
        "/v1/conversation-sessions",
        headers=_bearer(host_token),
        json={
            "mode": "REMOTE_QR",
            "hostLanguage": "en",
            "guestLanguage": "zh-CN",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_http_host_routes_require_principal_but_invite_claim_is_public(tmp_path) -> None:
    context, host, host_token, _outsider_token, client = _enterprise_conversation_client(
        tmp_path
    )

    with client:
        unauthenticated = client.post(
            "/v1/conversation-sessions",
            json={
                "mode": "REMOTE_QR",
                "hostLanguage": "en",
                "guestLanguage": "zh-CN",
            },
        )
        created = _create_remote_http(client, host_token)
        claimed = client.post(
            "/public/v1/conversation-sessions/claim",
            json={"inviteToken": created["invite"]["inviteToken"]},
        )
        audit = context.voice_audit_log.list_events()

    assert unauthenticated.status_code == 401
    assert claimed.status_code == 200
    assert claimed.json()["participantRole"] == "GUEST"
    created_audit = next(
        event for event in audit if event["action"] == "conversation.session.created"
    )
    assert created_audit["actorId"] == host["id"]
    assert created_audit["metadata"] == {
        "mode": "REMOTE_QR",
        "host_language": "en",
        "guest_language": "zh-CN",
        "participant_count": 1,
        "expires_at": created["session"]["expiresAt"],
    }
    serialized_audit = repr(audit)
    assert created["hostAccessToken"] not in serialized_audit
    assert created["invite"]["inviteToken"] not in serialized_audit


def test_http_public_turn_round_trip_and_uniform_invite_failure(tmp_path) -> None:
    _context, _host, host_token, _outsider_token, client = (
        _enterprise_conversation_client(tmp_path)
    )

    with client:
        created = _create_remote_http(client, host_token)
        claim_payload = {"inviteToken": created["invite"]["inviteToken"]}
        claimed = client.post(
            "/public/v1/conversation-sessions/claim", json=claim_payload
        )
        replayed_claim = client.post(
            "/public/v1/conversation-sessions/claim", json=claim_payload
        )
        unknown_claim = client.post(
            "/public/v1/conversation-sessions/claim",
            json={"inviteToken": "unknown-token-abcdefghijklmnopqrstuvwxyz"},
        )
        session_id = created["session"]["id"]
        published = client.post(
            f"/public/v1/conversation-sessions/{session_id}/turns",
            headers={
                "X-VoxAgent-Conversation-Token": created["hostAccessToken"]
            },
            json={
                "clientEventId": "host-http-turn-1",
                "speechEvent": _speech_event(session_id),
                "translationEvent": _translation_event(),
            },
        )
        replay = client.get(
            f"/public/v1/conversation-sessions/{session_id}/turns?afterSequence=0",
            headers={
                "X-VoxAgent-Conversation-Token": claimed.json()["accessToken"]
            },
        )

    assert claimed.status_code == 200
    assert replayed_claim.status_code == unknown_claim.status_code == 404
    assert replayed_claim.json() == unknown_claim.json()
    assert published.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["turns"] == [published.json()]


def test_http_host_end_requires_original_principal_and_host_capability(tmp_path) -> None:
    _context, _host, host_token, outsider_token, client = (
        _enterprise_conversation_client(tmp_path)
    )

    with client:
        created = _create_remote_http(client, host_token)
        session_id = created["session"]["id"]
        headers = {
            **_bearer(outsider_token),
            "X-VoxAgent-Conversation-Token": created["hostAccessToken"],
        }
        outsider = client.post(
            f"/v1/conversation-sessions/{session_id}/end",
            headers=headers,
        )
        ended = client.post(
            f"/v1/conversation-sessions/{session_id}/end",
            headers={
                **_bearer(host_token),
                "X-VoxAgent-Conversation-Token": created["hostAccessToken"],
            },
        )

    assert outsider.status_code == 403
    assert ended.status_code == 200
    assert ended.json()["status"] == "ENDED"


def test_http_contract_rejects_coercion_and_hidden_turn_fields(tmp_path) -> None:
    _context, _host, host_token, _outsider_token, client = (
        _enterprise_conversation_client(tmp_path)
    )

    with client:
        invalid_mode = client.post(
            "/v1/conversation-sessions",
            headers=_bearer(host_token),
            json={
                "mode": True,
                "hostLanguage": "en",
                "guestLanguage": "zh-CN",
            },
        )
        created = _create_remote_http(client, host_token)
        session_id = created["session"]["id"]
        speech = _speech_event(session_id)
        speech["hiddenCredential"] = "must-not-be-retained"
        hidden = client.post(
            f"/public/v1/conversation-sessions/{session_id}/turns",
            headers={
                "X-VoxAgent-Conversation-Token": created["hostAccessToken"]
            },
            json={
                "clientEventId": "hidden-field",
                "speechEvent": speech,
                "translationEvent": _translation_event(),
            },
        )

    assert invalid_mode.status_code == 422
    assert hidden.status_code in {400, 422}
