"""HTTP boundary for ephemeral Vox Agent conversation sessions."""

from __future__ import annotations

from typing import Annotated, Callable, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..app_context import AppContext
from ..auth import Principal, require_principal
from ..conversation_sessions import (
    ConversationAccessDenied,
    ConversationCapacityExceeded,
    ConversationNotFound,
    ConversationSessionError,
    ConversationTurnCapacityExceeded,
    InvalidConversationToken,
    InvalidConversationTurn,
    InviteUnavailable,
    ReplayGap,
    TurnIdempotencyConflict,
)

LanguageCode = Annotated[
    str,
    Field(min_length=2, max_length=35, pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"),
]
NonnegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
Confidence = Annotated[float, Field(ge=0, le=1, strict=True)]


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class CreateConversationSessionRequest(ContractModel):
    mode: Literal["FACE_TO_FACE", "REMOTE_QR"]
    host_language: LanguageCode
    guest_language: LanguageCode

    @model_validator(mode="after")
    def require_two_languages(self):
        if self.host_language == self.guest_language:
            raise ValueError("hostLanguage and guestLanguage must differ")
        return self


class ClaimConversationInviteRequest(ContractModel):
    invite_token: Annotated[str, Field(min_length=1, max_length=512)]


class SpeechLanguage(ContractModel):
    code: LanguageCode
    confidence: Confidence


class SpeechSpeaker(ContractModel):
    id: Annotated[str, Field(min_length=1, max_length=128)]
    label: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class SpeechTranscriptPayload(ContractModel):
    segment_id: Annotated[str, Field(min_length=1, max_length=128)]
    revision: PositiveInt
    text: Annotated[str, Field(min_length=1, max_length=32_000)]
    language: SpeechLanguage
    speaker: SpeechSpeaker
    audio_start_ms: NonnegativeInt
    audio_end_ms: NonnegativeInt
    confidence: Confidence

    @model_validator(mode="after")
    def validate_audio_range(self):
        if self.audio_end_ms < self.audio_start_ms:
            raise ValueError("audioEndMs must be >= audioStartMs")
        return self


class SpeechFinalEvent(ContractModel):
    schema_name: Literal["voxagent.speech.event"] = Field(alias="schema")
    schema_version: Literal[1]
    event_id: Annotated[str, Field(min_length=1, max_length=128)]
    type: Literal["transcript.final"]
    stream_id: Annotated[str, Field(min_length=1, max_length=128)]
    session_id: Annotated[str, Field(min_length=1, max_length=160)]
    sequence: PositiveInt
    monotonic_ms: NonnegativeInt
    emitted_at: Annotated[str, Field(min_length=1, max_length=64)]
    payload: SpeechTranscriptPayload


class SpeechPartialEvent(ContractModel):
    schema_name: Literal["voxagent.speech.event"] = Field(alias="schema")
    schema_version: Literal[1]
    event_id: Annotated[str, Field(min_length=1, max_length=128)]
    type: Literal["transcript.partial"]
    stream_id: Annotated[str, Field(min_length=1, max_length=128)]
    session_id: Annotated[str, Field(min_length=1, max_length=160)]
    sequence: PositiveInt
    monotonic_ms: NonnegativeInt
    emitted_at: Annotated[str, Field(min_length=1, max_length=64)]
    payload: SpeechTranscriptPayload


class TranslationCitation(ContractModel):
    stream_id: Annotated[str, Field(min_length=1, max_length=128)]
    segment_id: Annotated[str, Field(min_length=1, max_length=128)]
    revision: PositiveInt
    audio_start_ms: NonnegativeInt
    audio_end_ms: NonnegativeInt

    @model_validator(mode="after")
    def validate_audio_range(self):
        if self.audio_end_ms < self.audio_start_ms:
            raise ValueError("audioEndMs must be >= audioStartMs")
        return self


class TranslationGlossaryHit(ContractModel):
    source_text: Annotated[str, Field(min_length=1, max_length=256)]
    target_text: Annotated[str, Field(min_length=1, max_length=256)]


class TranslationFinalPayload(ContractModel):
    source_language: LanguageCode
    target_language: LanguageCode
    source_segments: Annotated[list[TranslationCitation], Field(min_length=1, max_length=8)]
    text: Annotated[str, Field(min_length=1, max_length=32_000)]
    confidence: Confidence
    provider: Annotated[str, Field(min_length=1, max_length=128)]
    model: Annotated[str, Field(min_length=1, max_length=256)]
    glossary_hits: Annotated[list[TranslationGlossaryHit], Field(max_length=128)]


class TranslationFinalEvent(ContractModel):
    schema_name: Literal["voxagent.translation.event"] = Field(alias="schema")
    schema_version: Literal[1]
    event_id: Annotated[str, Field(min_length=1, max_length=128)]
    translation_id: Annotated[str, Field(min_length=1, max_length=128)]
    type: Literal["translation.final"]
    emitted_at: Annotated[str, Field(min_length=1, max_length=64)]
    latency_ms: NonnegativeInt
    payload: TranslationFinalPayload


class PublishConversationTurnRequest(ContractModel):
    client_event_id: Annotated[str, Field(min_length=1, max_length=160)]
    speech_event: SpeechFinalEvent
    translation_event: TranslationFinalEvent

    @field_validator("speech_event", "translation_event", mode="before")
    @classmethod
    def reject_non_object_events(cls, value):
        if not isinstance(value, dict):
            raise ValueError("conversation events must be objects")
        return value


class PublishConversationLiveRequest(ContractModel):
    speech_event: SpeechPartialEvent

    @field_validator("speech_event", mode="before")
    @classmethod
    def reject_non_object_event(cls, value):
        if not isinstance(value, dict):
            raise ValueError("conversation live event must be an object")
        return value


def create_router(context: AppContext) -> APIRouter:
    router = APIRouter(tags=["conversation-sessions"])
    broker = context.conversation_session_broker

    @router.post("/v1/conversation-sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(
        payload: CreateConversationSessionRequest,
        principal: Principal = Depends(require_principal),
    ):
        created = _call(
            lambda: broker.create_session(
                owner_user_id=principal.user_id,
                workspace_id=principal.org_id,
                mode=payload.mode,
                host_language=payload.host_language,
                guest_language=payload.guest_language,
            )
        )
        session = created["session"]
        context.voice_audit_log.record(
            "conversation.session.created",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            target_id=session["id"],
            metadata={
                "mode": session["mode"],
                "host_language": session["hostLanguage"],
                "guest_language": session["guestLanguage"],
                "participant_count": session["participantCount"],
                "expires_at": session["expiresAt"],
            },
        )
        return created

    @router.post("/v1/conversation-sessions/{session_id}/end")
    async def end_session(
        session_id: str,
        principal: Principal = Depends(require_principal),
        access_token: str | None = Header(
            None, alias="X-VoxAgent-Conversation-Token"
        ),
    ):
        ended = _call(
            lambda: broker.end_session(
                session_id,
                _require_token(access_token),
                owner_user_id=principal.user_id,
                workspace_id=principal.org_id,
            )
        )
        context.voice_audit_log.record(
            "conversation.session.ended",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            target_id=session_id,
            metadata={
                "mode": ended["mode"],
                "participant_count": ended["participantCount"],
                "next_sequence": ended["nextSequence"],
                "expires_at": ended["expiresAt"],
            },
        )
        return ended

    @router.post("/public/v1/conversation-sessions/claim")
    async def claim_invite(payload: ClaimConversationInviteRequest):
        claimed = _call(lambda: broker.claim_invite(payload.invite_token))
        session = claimed["session"]
        context.voice_audit_log.record(
            "conversation.invite.claimed",
            target_id=session["id"],
            metadata={
                "mode": session["mode"],
                "participant_role": claimed["participantRole"],
                "participant_count": session["participantCount"],
                "expires_at": session["expiresAt"],
            },
        )
        return claimed

    @router.post("/public/v1/conversation-sessions/{session_id}/live")
    async def publish_live(
        session_id: str,
        payload: PublishConversationLiveRequest,
        access_token: str | None = Header(
            None, alias="X-VoxAgent-Conversation-Token"
        ),
    ):
        draft = _call(
            lambda: broker.publish_live(
                session_id,
                _require_token(access_token),
                speech_event=payload.speech_event.model_dump(
                    by_alias=True,
                    mode="json",
                ),
            )
        )
        context.voice_audit_log.record(
            "conversation.live.published",
            actor_id=f"conversation-participant:{draft['participantRole']}",
            target_id=session_id,
            metadata={
                "participant_role": draft["participantRole"],
                "stream_id": draft["streamId"],
                "segment_id": draft["segmentId"],
                "revision": draft["revision"],
                "source_language": draft["sourceLanguage"],
                "target_language": draft["targetLanguage"],
            },
        )
        return draft

    @router.get("/public/v1/conversation-sessions/{session_id}/live")
    async def read_live(
        session_id: str,
        access_token: str | None = Header(
            None, alias="X-VoxAgent-Conversation-Token"
        ),
    ):
        return _call(
            lambda: broker.read_live(
                session_id,
                _require_token(access_token),
            )
        )

    @router.get("/public/v1/conversation-sessions/{session_id}/turns")
    async def replay_turns(
        session_id: str,
        after_sequence: int = Query(0, alias="afterSequence", ge=0),
        access_token: str | None = Header(
            None, alias="X-VoxAgent-Conversation-Token"
        ),
    ):
        return _call(
            lambda: broker.replay_turns(
                session_id,
                _require_token(access_token),
                after_sequence=after_sequence,
            )
        )

    @router.post("/public/v1/conversation-sessions/{session_id}/turns")
    async def publish_turn(
        session_id: str,
        payload: PublishConversationTurnRequest,
        access_token: str | None = Header(
            None, alias="X-VoxAgent-Conversation-Token"
        ),
    ):
        turn = _call(
            lambda: broker.publish_turn(
                session_id,
                _require_token(access_token),
                client_event_id=payload.client_event_id,
                speech_event=payload.speech_event.model_dump(by_alias=True, mode="json"),
                translation_event=payload.translation_event.model_dump(
                    by_alias=True, mode="json"
                ),
            )
        )
        context.voice_audit_log.record(
            "conversation.turn.published",
            target_id=session_id,
            metadata={
                "participant_role": turn["participantRole"],
                "sequence": turn["sequence"],
                "source_language": turn["translationEvent"]["payload"][
                    "sourceLanguage"
                ],
                "target_language": turn["translationEvent"]["payload"][
                    "targetLanguage"
                ],
            },
        )
        return turn

    return router


def _require_token(value: str | None) -> str:
    if value is None or not value.strip():
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "conversation token required"
        )
    return value


def _call(operation: Callable[[], dict]) -> dict:
    try:
        return operation()
    except HTTPException:
        raise
    except (ConversationNotFound, InviteUnavailable) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (ConversationCapacityExceeded, ConversationTurnCapacityExceeded) as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            str(exc),
            headers={"Retry-After": "60"},
        ) from exc
    except InvalidConversationToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except ConversationAccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ReplayGap as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "conversation_replay_gap",
                "earliestSequence": exc.earliest_sequence,
                "nextSequence": exc.next_sequence,
            },
        ) from exc
    except TurnIdempotencyConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (InvalidConversationTurn, ConversationSessionError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
