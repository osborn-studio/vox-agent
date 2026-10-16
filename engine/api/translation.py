"""HTTP transport for cited realtime translation events."""

from __future__ import annotations

import hashlib
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..app_context import AppContext
from ..auth import Principal, require_principal
from ..translation import (
    TranslationError,
    TranslationIdempotencyConflict,
)
from ..streaming_speech import (
    InvalidResumeToken,
    SourceLanguageMismatch,
    SourceSegmentNotFound,
    SpeechStreamError,
    StreamNotFound,
)

LanguageCode = Annotated[
    str,
    Field(min_length=2, max_length=35, pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"),
]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class TranslationSourceReference(ContractModel):
    stream_id: Annotated[str, Field(min_length=1, max_length=128)]
    segment_id: Annotated[str, Field(min_length=1, max_length=128)]
    revision: PositiveInt


class TranslationGlossaryEntry(ContractModel):
    source_text: Annotated[str, Field(min_length=1, max_length=256)]
    target_text: Annotated[str, Field(min_length=1, max_length=256)]


class TranslationRequest(ContractModel):
    source_language: LanguageCode
    target_language: LanguageCode
    segments: Annotated[list[TranslationSourceReference], Field(min_length=1, max_length=8)]
    glossary: Annotated[list[TranslationGlossaryEntry], Field(max_length=128)] = Field(
        default_factory=list
    )
    allow_remote: bool = False
    idempotency_key: Annotated[str, Field(min_length=1, max_length=160)]

    @field_validator("allow_remote", mode="before")
    @classmethod
    def reject_non_boolean_consent(cls, value):
        if not isinstance(value, bool):
            raise ValueError("allowRemote must be a boolean")
        return value


def create_router(context: AppContext) -> APIRouter:
    router = APIRouter(prefix="/v1/translations", tags=["translation"])

    @router.post("")
    def translate(
        payload: TranslationRequest,
        principal: Principal = Depends(require_principal),
        resume_token: str | None = Header(None, alias="X-VoxAgent-Stream-Token"),
    ):
        references = [segment.model_dump(by_alias=True) for segment in payload.segments]
        source_stream_id = references[0]["streamId"]
        resolved_segments = _resolve_sources(
            context,
            source_stream_id,
            _require_token(resume_token),
            references,
            payload.source_language,
        )
        event = _call(
            lambda: context.translation_pipeline.translate(
                source_language=payload.source_language,
                target_language=payload.target_language,
                segments=resolved_segments,
                glossary=[entry.model_dump(by_alias=True) for entry in payload.glossary],
                allow_remote=payload.allow_remote,
                idempotency_key=_scoped_idempotency_key(
                    principal, payload.idempotency_key
                ),
            )
        )
        event_payload = event["payload"]
        metadata = {
            "source_language": payload.source_language,
            "target_language": payload.target_language,
            "source_segment_ids": [segment.segment_id for segment in payload.segments],
            "source_stream_ids": sorted({segment.stream_id for segment in payload.segments}),
            "allow_remote": payload.allow_remote,
            "event_type": event["type"],
        }
        if event["type"] == "translation.final":
            metadata.update(
                {
                    "provider": event_payload["provider"],
                    "model": event_payload["model"],
                    "confidence": event_payload["confidence"],
                }
            )
            action = "translation.completed"
        else:
            metadata.update(
                {
                    "failure_code": event_payload["code"],
                    "retriable": event_payload["retriable"],
                }
            )
            action = "translation.failed"
        context.voice_audit_log.record(
            action,
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            target_id=event["translationId"],
            metadata=metadata,
        )
        return event

    return router


def _scoped_idempotency_key(principal: Principal, client_key: str) -> str:
    encoded = "\0".join(
        ("translation.v1", principal.org_id, principal.user_id, client_key)
    ).encode("utf-8")
    return f"http-{hashlib.sha256(encoded).hexdigest()}"


def _require_token(value: str | None) -> str:
    if value is None or not value.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "speech stream token required")
    return value


def _resolve_sources(
    context: AppContext,
    stream_id: str,
    resume_token: str,
    references: list[dict],
    source_language: str,
) -> list[dict]:
    try:
        return context.speech_stream_broker.resolve_final_segments(
            stream_id,
            resume_token,
            references=references,
            source_language=source_language,
        )
    except StreamNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidResumeToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except (SourceSegmentNotFound, SourceLanguageMismatch) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except SpeechStreamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _call(operation: Callable[[], dict]) -> dict:
    try:
        return operation()
    except TranslationIdempotencyConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except TranslationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
