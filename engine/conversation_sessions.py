"""Ephemeral, token-scoped Vox Agent conversation sessions.

This module deliberately does not persist sessions or transcript turns.  A host
principal creates a bounded session, then participants authenticate with random
session-scoped capabilities.  Remote invites are single-use and short-lived.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import time
from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable
from urllib.parse import quote
from uuid import uuid4

CONVERSATION_SESSION_SCHEMA = "voxagent.conversation.session"
CONVERSATION_SESSION_SCHEMA_VERSION = 1
CONVERSATION_TURN_SCHEMA = "voxagent.conversation.turn"
CONVERSATION_TURN_SCHEMA_VERSION = 1
CONVERSATION_LIVE_SCHEMA = "voxagent.conversation.live"
CONVERSATION_LIVE_SCHEMA_VERSION = 1

_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_MODES = {"FACE_TO_FACE", "REMOTE_QR"}
_ROLES = {"HOST", "GUEST"}


class ConversationSessionError(ValueError):
    """Base class for safe conversation-protocol errors."""


class ConversationCapacityExceeded(ConversationSessionError):
    pass


class ConversationTurnCapacityExceeded(ConversationSessionError):
    pass


class ConversationNotFound(ConversationSessionError):
    pass


class InvalidConversationToken(ConversationSessionError):
    pass


class ConversationAccessDenied(ConversationSessionError):
    pass


class InvalidConversationTurn(ConversationSessionError):
    pass


class InviteUnavailable(ConversationSessionError):
    """An invite is unknown, expired, or already claimed.

    Those cases intentionally share one error so callers cannot enumerate live
    sessions or infer whether another participant already claimed a secret.
    """


class TurnIdempotencyConflict(ConversationSessionError):
    pass


class ReplayGap(ConversationSessionError):
    def __init__(self, earliest_sequence: int, next_sequence: int) -> None:
        self.earliest_sequence = earliest_sequence
        self.next_sequence = next_sequence
        super().__init__(
            f"replay cursor predates retained turns; earliest={earliest_sequence}"
        )


@dataclass(slots=True)
class _Participant:
    role: str
    token_digest: bytes = field(repr=False)


@dataclass(slots=True)
class _ConversationSession:
    session_id: str
    owner_user_id: str = field(repr=False)
    workspace_id: str = field(repr=False)
    mode: str
    host_language: str
    guest_language: str
    status: str
    created_clock: float
    expires_clock: float
    created_at: datetime
    expires_at: datetime
    invite_expires_clock: float | None
    invite_expires_at: datetime | None
    invite_digest: bytes | None = field(default=None, repr=False)
    invite_claimed: bool = False
    participants: dict[str, _Participant] = field(default_factory=dict, repr=False)
    turns: deque[dict] = field(default_factory=deque)
    idempotency: OrderedDict[tuple[str, str], tuple[str, dict]] = field(
        default_factory=OrderedDict, repr=False
    )
    live_drafts: dict[str, dict] = field(default_factory=dict, repr=False)
    finalized_segments: OrderedDict[tuple[str, str, str], None] = field(
        default_factory=OrderedDict, repr=False
    )
    next_sequence: int = 1


class ConversationSessionBroker:
    """Thread-safe authority for face-to-face and remote QR sessions."""

    def __init__(
        self,
        *,
        invite_ttl_seconds: int = 5 * 60,
        session_ttl_seconds: int = 60 * 60,
        max_sessions: int = 64,
        max_turns_per_session: int = 1_000,
        max_total_turns_per_session: int = 10_000,
        clock: Callable[[], float] | None = None,
        utcnow: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(invite_ttl_seconds, int) or isinstance(
            invite_ttl_seconds, bool
        ) or invite_ttl_seconds < 1:
            raise ValueError("invite_ttl_seconds must be a positive integer")
        if not isinstance(session_ttl_seconds, int) or isinstance(
            session_ttl_seconds, bool
        ) or session_ttl_seconds < invite_ttl_seconds:
            raise ValueError("session_ttl_seconds must cover the invite window")
        if not isinstance(max_sessions, int) or isinstance(max_sessions, bool) or max_sessions < 1:
            raise ValueError("max_sessions must be a positive integer")
        if not isinstance(max_turns_per_session, int) or isinstance(
            max_turns_per_session, bool
        ) or max_turns_per_session < 1:
            raise ValueError("max_turns_per_session must be a positive integer")
        if not isinstance(max_total_turns_per_session, int) or isinstance(
            max_total_turns_per_session, bool
        ) or max_total_turns_per_session < max_turns_per_session:
            raise ValueError(
                "max_total_turns_per_session must cover the replay window"
            )
        self.invite_ttl_seconds = invite_ttl_seconds
        self.session_ttl_seconds = session_ttl_seconds
        self.max_sessions = max_sessions
        self.max_turns_per_session = max_turns_per_session
        self.max_total_turns_per_session = max_total_turns_per_session
        self._clock = clock or time.monotonic
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._sessions: dict[str, _ConversationSession] = {}
        self._lock = RLock()

    def create_session(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        mode: str,
        host_language: str,
        guest_language: str,
    ) -> dict:
        owner = _required_text(owner_user_id, "owner_user_id", max_length=160)
        workspace = _required_text(workspace_id, "workspace_id", max_length=160)
        if mode not in _MODES:
            raise ConversationSessionError("unsupported conversation mode")
        host_language = _language(host_language)
        guest_language = _language(guest_language)
        if host_language == guest_language:
            raise ConversationSessionError("conversation languages must differ")

        with self._lock:
            now_clock = self._clock()
            self._prune_expired_locked(now_clock)
            if len(self._sessions) >= self.max_sessions:
                raise ConversationCapacityExceeded(
                    f"conversation session capacity reached ({self.max_sessions})"
                )

            now = _aware_utc(self._utcnow())
            expires_at = now + timedelta(seconds=self.session_ttl_seconds)
            session_id = f"conv_{uuid4().hex}"
            host_token = self._new_token()
            session = _ConversationSession(
                session_id=session_id,
                owner_user_id=owner,
                workspace_id=workspace,
                mode=mode,
                host_language=host_language,
                guest_language=guest_language,
                status="ACTIVE" if mode == "FACE_TO_FACE" else "WAITING",
                created_clock=now_clock,
                expires_clock=now_clock + self.session_ttl_seconds,
                created_at=now,
                expires_at=expires_at,
                invite_expires_clock=(
                    now_clock + self.invite_ttl_seconds if mode == "REMOTE_QR" else None
                ),
                invite_expires_at=(
                    now + timedelta(seconds=self.invite_ttl_seconds)
                    if mode == "REMOTE_QR"
                    else None
                ),
            )
            session.participants["HOST"] = _Participant(
                role="HOST", token_digest=_token_digest(host_token)
            )

            local_guest_token: str | None = None
            invite: dict | None = None
            if mode == "FACE_TO_FACE":
                local_guest_token = self._new_token()
                session.participants["GUEST"] = _Participant(
                    role="GUEST", token_digest=_token_digest(local_guest_token)
                )
            else:
                invite_token = self._new_token()
                session.invite_digest = _token_digest(invite_token)
                invite = {
                    "inviteToken": invite_token,
                    "qrPayload": (
                        "voxagent://conversation/join#v=1&token="
                        f"{quote(invite_token, safe='')}"
                    ),
                    "expiresAt": _isoformat(session.invite_expires_at),
                }

            self._sessions[session_id] = session
            return {
                "session": self._snapshot(session),
                "hostAccessToken": host_token,
                "localGuestAccessToken": local_guest_token,
                "invite": invite,
            }

    def claim_invite(self, invite_token: str) -> dict:
        supplied = _token_digest(_required_text(invite_token, "invite_token", max_length=512))
        with self._lock:
            now_clock = self._clock()
            self._prune_expired_locked(now_clock)
            matched: _ConversationSession | None = None
            for session in self._sessions.values():
                digest = session.invite_digest or b"\x00" * hashlib.sha256().digest_size
                if hmac.compare_digest(digest, supplied):
                    matched = session
            if (
                matched is None
                or matched.invite_claimed
                or matched.invite_expires_clock is None
                or now_clock >= matched.invite_expires_clock
                or matched.status != "WAITING"
            ):
                raise InviteUnavailable("conversation invite is unavailable")

            guest_token = self._new_token()
            matched.participants["GUEST"] = _Participant(
                role="GUEST", token_digest=_token_digest(guest_token)
            )
            matched.invite_claimed = True
            matched.invite_digest = None
            matched.status = "ACTIVE"
            return {
                "session": self._snapshot(matched),
                "participantRole": "GUEST",
                "accessToken": guest_token,
            }

    def publish_turn(
        self,
        session_id: str,
        access_token: str,
        *,
        client_event_id: str,
        speech_event: dict,
        translation_event: dict,
    ) -> dict:
        client_event_id = _required_text(
            client_event_id, "client_event_id", max_length=160
        )
        with self._lock:
            session = self._active_session_locked(session_id)
            participant = self._authenticated_participant_locked(session, access_token)
            if session.status != "ACTIVE":
                raise InvalidConversationTurn("conversation session is not active")
            if session.next_sequence > self.max_total_turns_per_session:
                raise ConversationTurnCapacityExceeded(
                    "conversation turn capacity reached"
                )

            normalized_speech, normalized_translation = _validated_turn_events(
                session=session,
                role=participant.role,
                speech_event=speech_event,
                translation_event=translation_event,
            )
            fingerprint = _fingerprint(
                {"speechEvent": normalized_speech, "translationEvent": normalized_translation}
            )
            idempotency_key = (participant.role, client_event_id)
            existing = session.idempotency.get(idempotency_key)
            if existing is not None:
                existing_fingerprint, existing_turn = existing
                if not hmac.compare_digest(existing_fingerprint, fingerprint):
                    raise TurnIdempotencyConflict(
                        "client_event_id was already used for different content"
                    )
                session.idempotency.move_to_end(idempotency_key)
                self._record_final_segment_locked(
                    session,
                    participant.role,
                    normalized_speech,
                )
                return deepcopy(existing_turn)

            turn = {
                "schema": CONVERSATION_TURN_SCHEMA,
                "schemaVersion": CONVERSATION_TURN_SCHEMA_VERSION,
                "type": "conversation.turn",
                "eventId": f"conversation_event_{uuid4().hex}",
                "sessionId": session.session_id,
                "sequence": session.next_sequence,
                "participantRole": participant.role,
                "clientEventId": client_event_id,
                "speechEvent": normalized_speech,
                "translationEvent": normalized_translation,
                "createdAt": _isoformat(_aware_utc(self._utcnow())),
            }
            session.next_sequence += 1
            session.turns.append(turn)
            self._record_final_segment_locked(
                session,
                participant.role,
                normalized_speech,
            )
            session.idempotency[idempotency_key] = (fingerprint, turn)
            session.idempotency.move_to_end(idempotency_key)
            while len(session.turns) > self.max_turns_per_session:
                evicted = session.turns.popleft()
                evicted_key = (evicted["participantRole"], evicted["clientEventId"])
                session.idempotency.pop(evicted_key, None)
            return deepcopy(turn)

    def publish_live(
        self,
        session_id: str,
        access_token: str,
        *,
        speech_event: dict,
    ) -> dict:
        with self._lock:
            session = self._active_session_locked(session_id)
            participant = self._authenticated_participant_locked(session, access_token)
            if session.status != "ACTIVE":
                raise InvalidConversationTurn("conversation session is not active")
            speech = _validated_speech_direction(
                session=session,
                role=participant.role,
                speech_event=speech_event,
                expected_type="transcript.partial",
            )
            stream_id = speech["streamId"]
            segment_id = speech["payload"]["segmentId"]
            revision = speech["payload"]["revision"]
            source_language, target_language = (
                (session.host_language, session.guest_language)
                if participant.role == "HOST"
                else (session.guest_language, session.host_language)
            )
            segment_key = (participant.role, stream_id, segment_id)
            if segment_key in session.finalized_segments:
                raise InvalidConversationTurn(
                    "live draft cannot replace a finalized segment"
                )

            existing = session.live_drafts.get(participant.role)
            if (
                existing is not None
                and existing["streamId"] == stream_id
                and existing["segmentId"] == segment_id
            ):
                if revision < existing["revision"]:
                    raise InvalidConversationTurn(
                        "live draft revision must increase monotonically"
                    )
                if revision == existing["revision"]:
                    if hmac.compare_digest(
                        _fingerprint(speech),
                        _fingerprint(existing["speechEvent"]),
                    ):
                        return deepcopy(existing)
                    raise InvalidConversationTurn(
                        "live draft revision was reused for different content"
                    )

            draft = {
                "schema": CONVERSATION_LIVE_SCHEMA,
                "schemaVersion": CONVERSATION_LIVE_SCHEMA_VERSION,
                "type": "conversation.live",
                "sessionId": session.session_id,
                "participantRole": participant.role,
                "streamId": stream_id,
                "segmentId": segment_id,
                "revision": revision,
                "sourceLanguage": source_language,
                "targetLanguage": target_language,
                "speechEvent": speech,
                "updatedAt": _isoformat(_aware_utc(self._utcnow())),
            }
            session.live_drafts[participant.role] = draft
            return deepcopy(draft)

    def read_live(self, session_id: str, access_token: str) -> dict:
        with self._lock:
            session = self._active_session_locked(session_id)
            self._authenticated_participant_locked(session, access_token)
            if session.status != "ACTIVE":
                raise InvalidConversationTurn("conversation session is not active")
            return {
                "sessionId": session.session_id,
                "host": deepcopy(session.live_drafts.get("HOST")),
                "guest": deepcopy(session.live_drafts.get("GUEST")),
                "status": session.status,
            }

    def replay_turns(
        self,
        session_id: str,
        access_token: str,
        *,
        after_sequence: int = 0,
    ) -> dict:
        if not isinstance(after_sequence, int) or isinstance(after_sequence, bool):
            raise ConversationSessionError("after_sequence must be an integer")
        if after_sequence < 0:
            raise ConversationSessionError("after_sequence must not be negative")
        with self._lock:
            session = self._active_session_locked(session_id)
            self._authenticated_participant_locked(session, access_token)
            latest_sequence = session.next_sequence - 1
            if after_sequence > latest_sequence:
                raise ConversationSessionError("after_sequence is ahead of the session")
            earliest_sequence = (
                session.turns[0]["sequence"] if session.turns else session.next_sequence
            )
            if after_sequence < earliest_sequence - 1:
                raise ReplayGap(earliest_sequence, session.next_sequence)
            return {
                "sessionId": session.session_id,
                "turns": [
                    deepcopy(turn)
                    for turn in session.turns
                    if turn["sequence"] > after_sequence
                ],
                "nextSequence": session.next_sequence,
                "status": session.status,
            }

    def end_session(
        self,
        session_id: str,
        host_access_token: str,
        *,
        owner_user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict:
        with self._lock:
            session = self._active_session_locked(session_id)
            participant = self._authenticated_participant_locked(
                session, host_access_token
            )
            if participant.role != "HOST":
                raise InvalidConversationToken("host conversation token required")
            if (
                owner_user_id is not None
                and not hmac.compare_digest(session.owner_user_id, owner_user_id)
            ) or (
                workspace_id is not None
                and not hmac.compare_digest(session.workspace_id, workspace_id)
            ):
                raise ConversationAccessDenied(
                    "conversation session belongs to another principal"
                )
            session.status = "ENDED"
            ended = self._snapshot(session)
            del self._sessions[session.session_id]
            return ended

    def _active_session_locked(self, session_id: str) -> _ConversationSession:
        session_id = _required_text(session_id, "session_id", max_length=160)
        now_clock = self._clock()
        self._prune_expired_locked(now_clock)
        session = self._sessions.get(session_id)
        if session is None:
            raise ConversationNotFound("conversation session was not found")
        return session

    @staticmethod
    def _authenticated_participant_locked(
        session: _ConversationSession, access_token: str
    ) -> _Participant:
        supplied = _token_digest(
            _required_text(access_token, "access_token", max_length=512)
        )
        matched: _Participant | None = None
        for role in sorted(_ROLES):
            participant = session.participants.get(role)
            digest = (
                participant.token_digest
                if participant is not None
                else b"\x00" * hashlib.sha256().digest_size
            )
            if hmac.compare_digest(digest, supplied):
                matched = participant
        if matched is None:
            raise InvalidConversationToken("invalid conversation token")
        return matched

    def _new_token(self) -> str:
        return _required_text(self._token_factory(), "generated token", max_length=512)

    def _record_final_segment_locked(
        self,
        session: _ConversationSession,
        role: str,
        speech_event: dict,
    ) -> None:
        segment_key = (
            role,
            speech_event["streamId"],
            speech_event["payload"]["segmentId"],
        )
        session.finalized_segments[segment_key] = None
        session.finalized_segments.move_to_end(segment_key)
        while len(session.finalized_segments) > self.max_turns_per_session:
            session.finalized_segments.popitem(last=False)

        draft = session.live_drafts.get(role)
        if (
            draft is not None
            and draft["streamId"] == segment_key[1]
            and draft["segmentId"] == segment_key[2]
        ):
            del session.live_drafts[role]

    def _prune_expired_locked(self, now_clock: float) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now_clock >= session.expires_clock
            or (
                session.status == "WAITING"
                and session.invite_expires_clock is not None
                and now_clock >= session.invite_expires_clock
            )
        ]
        for session_id in expired:
            del self._sessions[session_id]

    @staticmethod
    def _snapshot(session: _ConversationSession) -> dict:
        return {
            "schema": CONVERSATION_SESSION_SCHEMA,
            "schemaVersion": CONVERSATION_SESSION_SCHEMA_VERSION,
            "id": session.session_id,
            "mode": session.mode,
            "status": session.status,
            "hostLanguage": session.host_language,
            "guestLanguage": session.guest_language,
            "participantCount": len(session.participants),
            "createdAt": _isoformat(session.created_at),
            "expiresAt": _isoformat(session.expires_at),
            "nextSequence": session.next_sequence,
        }


def _validated_turn_events(
    *,
    session: _ConversationSession,
    role: str,
    speech_event: object,
    translation_event: object,
) -> tuple[dict, dict]:
    if not isinstance(translation_event, dict):
        raise InvalidConversationTurn("conversation turn events must be objects")
    speech = _validated_speech_direction(
        session=session,
        role=role,
        speech_event=speech_event,
        expected_type="transcript.final",
    )
    translation = deepcopy(translation_event)
    _expect_keys(
        translation,
        {
            "schema",
            "schemaVersion",
            "eventId",
            "translationId",
            "type",
            "emittedAt",
            "latencyMs",
            "payload",
        },
        "translation event",
    )
    source_language, target_language = (
        (session.host_language, session.guest_language)
        if role == "HOST"
        else (session.guest_language, session.host_language)
    )
    speech_payload = speech.get("payload")

    if (
        translation.get("schema") != "voxagent.translation.event"
        or translation.get("schemaVersion") != 1
        or translation.get("type") != "translation.final"
    ):
        raise InvalidConversationTurn("translation event must be a final v1 event")
    translated = translation.get("payload")
    if not isinstance(translated, dict):
        raise InvalidConversationTurn("translation event payload is invalid")
    _expect_keys(
        translated,
        {
            "sourceLanguage",
            "targetLanguage",
            "sourceSegments",
            "text",
            "confidence",
            "provider",
            "model",
            "glossaryHits",
        },
        "translation payload",
    )
    if (
        translated.get("sourceLanguage") != source_language
        or translated.get("targetLanguage") != target_language
    ):
        raise InvalidConversationTurn("translation direction does not match participant")
    translated_text = translated.get("text")
    if not isinstance(translated_text, str) or not translated_text or len(translated_text) > 32_000:
        raise InvalidConversationTurn("translation text is invalid")

    stream_id = speech.get("streamId")
    segment_id = speech_payload.get("segmentId")
    revision = speech_payload.get("revision")
    audio_start_ms = speech_payload.get("audioStartMs")
    audio_end_ms = speech_payload.get("audioEndMs")
    references = translated.get("sourceSegments")
    if not isinstance(references, list) or len(references) != 1:
        raise InvalidConversationTurn("translation requires exactly one source citation")
    for reference in references:
        if not isinstance(reference, dict):
            raise InvalidConversationTurn("translation citation is invalid")
        _expect_keys(
            reference,
            {"streamId", "segmentId", "revision", "audioStartMs", "audioEndMs"},
            "translation citation",
        )
    expected = {
        "streamId": stream_id,
        "segmentId": segment_id,
        "revision": revision,
        "audioStartMs": audio_start_ms,
        "audioEndMs": audio_end_ms,
    }
    if not all(references[0].get(key) == value for key, value in expected.items()):
        raise InvalidConversationTurn("translation citation does not match speech")
    return speech, translation


def _validated_speech_direction(
    *,
    session: _ConversationSession,
    role: str,
    speech_event: object,
    expected_type: str,
) -> dict:
    if not isinstance(speech_event, dict):
        raise InvalidConversationTurn("speech event must be an object")
    speech = deepcopy(speech_event)
    _expect_keys(
        speech,
        {
            "schema",
            "schemaVersion",
            "eventId",
            "type",
            "streamId",
            "sessionId",
            "sequence",
            "monotonicMs",
            "emittedAt",
            "payload",
        },
        "speech event",
    )
    if (
        speech.get("schema") != "voxagent.speech.event"
        or type(speech.get("schemaVersion")) is not int
        or speech.get("schemaVersion") != 1
        or speech.get("type") != expected_type
        or speech.get("sessionId") != session.session_id
    ):
        raise InvalidConversationTurn("speech event does not match the conversation")
    _bounded_text(speech.get("eventId"), "speech event id", 128)
    _bounded_text(speech.get("streamId"), "speech stream id", 128)
    _strict_integer(speech.get("sequence"), "speech sequence", minimum=1)
    _strict_integer(speech.get("monotonicMs"), "speech monotonic time", minimum=0)
    _bounded_text(speech.get("emittedAt"), "speech emitted time", 64)

    payload = speech.get("payload")
    if not isinstance(payload, dict):
        raise InvalidConversationTurn("speech event payload is invalid")
    _expect_keys(
        payload,
        {
            "segmentId",
            "revision",
            "text",
            "language",
            "speaker",
            "audioStartMs",
            "audioEndMs",
            "confidence",
        },
        "speech payload",
    )
    _bounded_text(payload.get("segmentId"), "speech segment id", 128)
    _strict_integer(payload.get("revision"), "speech revision", minimum=1)
    _bounded_text(payload.get("text"), "speech text", 32_000)
    audio_start = _strict_integer(
        payload.get("audioStartMs"),
        "speech audio start",
        minimum=0,
    )
    audio_end = _strict_integer(
        payload.get("audioEndMs"),
        "speech audio end",
        minimum=0,
    )
    if audio_end < audio_start:
        raise InvalidConversationTurn("speech audio range is invalid")
    _confidence(payload.get("confidence"), "speech confidence")

    source_language = (
        session.host_language if role == "HOST" else session.guest_language
    )
    language = payload.get("language")
    if not isinstance(language, dict):
        raise InvalidConversationTurn("speech language payload is invalid")
    _expect_keys(language, {"code", "confidence"}, "speech language")
    if language.get("code") != source_language:
        raise InvalidConversationTurn(
            "speech language does not match participant direction"
        )
    _confidence(language.get("confidence"), "speech language confidence")

    speaker = payload.get("speaker")
    if not isinstance(speaker, dict):
        raise InvalidConversationTurn("speech speaker payload is invalid")
    _expect_keys(speaker, {"id", "label"}, "speech speaker")
    _bounded_text(speaker.get("id"), "speech speaker id", 128)
    label = speaker.get("label")
    if label is not None:
        _bounded_text(label, "speech speaker label", 128)
    return speech


def _bounded_text(value: object, field_name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise InvalidConversationTurn(
            f"{field_name} must contain between 1 and {max_length} characters"
        )
    return value


def _strict_integer(value: object, field_name: str, *, minimum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        raise InvalidConversationTurn(f"{field_name} must be an integer >= {minimum}")
    return value


def _confidence(value: object, field_name: str) -> float | int:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or (isinstance(value, float) and not math.isfinite(value))
        or value < 0
        or value > 1
    ):
        raise InvalidConversationTurn(f"{field_name} must be between 0 and 1")
    return value


def _expect_keys(value: dict, expected: set[str], field_name: str) -> None:
    if set(value) != expected:
        raise InvalidConversationTurn(f"{field_name} fields do not match the contract")


def _required_text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise ConversationSessionError(f"{field_name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ConversationSessionError(
            f"{field_name} must contain between 1 and {max_length} characters"
        )
    return normalized


def _language(value: object) -> str:
    language = _required_text(value, "language", max_length=64)
    if not _LANGUAGE_RE.fullmatch(language):
        raise ConversationSessionError("language must be a BCP 47 tag")
    return language


def _token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("utcnow must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
