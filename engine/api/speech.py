"""HTTP transport for the versioned streaming speech event contract."""

from __future__ import annotations

import asyncio
from typing import Annotated, Callable, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..app_context import AppContext
from ..auth import Principal, require_principal
from ..streaming_speech import (
    InvalidCursor,
    InvalidResumeToken,
    ReplayGap,
    RevisionConflict,
    SpeechStreamError,
    StreamCapacityExceeded,
    StreamNotFound,
    StreamTerminal,
)
from ..speech_audio import (
    AudioChunkConflict,
    AudioChunkLimitExceeded,
    SpeechAudioChunkStore,
    SpeechAudioError,
)
from ..speech_asr import SpeechAsrProviderError

NonnegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
Confidence = Annotated[float, Field(ge=0, le=1, strict=True)]
LanguageCode = Annotated[
    str,
    Field(min_length=2, max_length=35, pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"),
]
T = TypeVar("T")


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class SpeechLanguage(ContractModel):
    code: LanguageCode
    confidence: Confidence

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_boolean_confidence(cls, value):
        if isinstance(value, bool):
            raise ValueError("confidence cannot be boolean")
        return value


class SpeechSpeaker(ContractModel):
    id: Annotated[str, Field(min_length=1, max_length=128)]
    label: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class CreateSpeechStreamRequest(ContractModel):
    session_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    language_hints: Annotated[list[LanguageCode], Field(max_length=8)] = Field(
        default_factory=list
    )


class SpeechTranscriptRequest(ContractModel):
    segment_id: Annotated[str, Field(min_length=1, max_length=128)]
    revision: PositiveInt
    text: Annotated[str, Field(min_length=1, max_length=32_000)]
    language: SpeechLanguage
    speaker: SpeechSpeaker
    audio_start_ms: NonnegativeInt
    audio_end_ms: NonnegativeInt
    confidence: Confidence
    monotonic_ms: NonnegativeInt
    idempotency_key: Annotated[str, Field(min_length=1, max_length=160)]

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_boolean_confidence(cls, value):
        if isinstance(value, bool):
            raise ValueError("confidence cannot be boolean")
        return value

    @model_validator(mode="after")
    def validate_audio_range(self):
        if self.audio_end_ms < self.audio_start_ms:
            raise ValueError("audioEndMs must be >= audioStartMs")
        return self


class CancelSpeechStreamRequest(ContractModel):
    reason: Annotated[str, Field(min_length=1, max_length=256)]
    cancelled_by: Annotated[str, Field(min_length=1, max_length=128)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=160)]


class CompleteSpeechStreamRequest(ContractModel):
    reason: Annotated[str, Field(min_length=1, max_length=256)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=160)]


def create_router(context: AppContext) -> APIRouter:
    router = APIRouter(prefix="/v1/speech/streams", tags=["speech-streaming"])
    broker = context.speech_stream_broker
    processor = context.speech_asr_processor
    audio_store = SpeechAudioChunkStore(broker)

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def create_stream(
        payload: CreateSpeechStreamRequest,
        principal: Principal = Depends(require_principal),
    ):
        result = _call(
            lambda: broker.create_stream(
                session_id=payload.session_id,
                language_hints=payload.language_hints,
            )
        )
        context.voice_audit_log.record(
            "speech.stream.created",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            target_id=result["streamId"],
            metadata={
                "session_id": payload.session_id,
                "language_hints": payload.language_hints,
                "schema_version": result["schemaVersion"],
            },
        )
        return result

    @router.get("/{stream_id}/events")
    async def replay_events(
        stream_id: str,
        after_sequence: int = Query(0, alias="afterSequence", ge=0),
        resume_token: str | None = Header(None, alias="X-VoxAgent-Stream-Token"),
        principal: Principal = Depends(require_principal),
    ):
        return _call(
            lambda: broker.replay(
                stream_id,
                _require_token(resume_token),
                after_sequence=after_sequence,
            )
        )

    @router.post("/{stream_id}/audio/chunks")
    async def append_audio_chunk(
        stream_id: str,
        request: Request,
        resume_token: str | None = Header(None, alias="X-VoxAgent-Stream-Token"),
        chunk_index: str | None = Header(
            None,
            alias="X-VoxAgent-Audio-Chunk-Index",
        ),
        sample_rate: str | None = Header(
            None,
            alias="X-VoxAgent-Audio-Sample-Rate",
        ),
        channels: str | None = Header(
            None,
            alias="X-VoxAgent-Audio-Channels",
        ),
        encoding: str | None = Header(
            None,
            alias="X-VoxAgent-Audio-Encoding",
        ),
        principal: Principal = Depends(require_principal),
    ):
        token = _require_token(resume_token)
        _call(lambda: audio_store.authorize(stream_id, token))
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != "application/octet-stream":
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "speech audio chunks require application/octet-stream",
            )
        audio = await _read_bounded_body(request, audio_store.max_chunk_bytes)
        acknowledgement = _call(
            lambda: audio_store.append(
                stream_id,
                token,
                chunk_index=_required_header_integer(
                    chunk_index,
                    "X-VoxAgent-Audio-Chunk-Index",
                ),
                sample_rate=_required_header_integer(
                    sample_rate,
                    "X-VoxAgent-Audio-Sample-Rate",
                ),
                channels=_required_header_integer(
                    channels,
                    "X-VoxAgent-Audio-Channels",
                ),
                encoding=(encoding or "").strip().lower(),
                audio=audio,
            )
        )
        snapshot = _call(lambda: audio_store.snapshot_if_due(stream_id, token))
        if snapshot is not None:
            try:
                await asyncio.to_thread(
                    processor.publish_partial,
                    snapshot,
                    token,
                )
            except SpeechAsrProviderError:
                _record_asr_failure(
                    context,
                    audio_store,
                    processor,
                    stream_id,
                    token,
                    principal,
                )
                raise _provider_failure_response() from None
            except Exception as exc:
                _call_raise(exc)
        return acknowledgement

    async def publish_transcript(
        stream_id: str,
        payload: SpeechTranscriptRequest,
        resume_token: str | None,
        event_type: str,
    ):
        return _call(
            lambda: broker.publish_transcript(
                stream_id,
                _require_token(resume_token),
                event_type=event_type,
                segment_id=payload.segment_id,
                revision=payload.revision,
                text=payload.text,
                language=payload.language.model_dump(),
                speaker=payload.speaker.model_dump(),
                audio_start_ms=payload.audio_start_ms,
                audio_end_ms=payload.audio_end_ms,
                confidence=payload.confidence,
                monotonic_ms=payload.monotonic_ms,
                idempotency_key=payload.idempotency_key,
            )
        )

    @router.post("/{stream_id}/partial")
    async def publish_partial(
        stream_id: str,
        payload: SpeechTranscriptRequest,
        resume_token: str | None = Header(None, alias="X-VoxAgent-Stream-Token"),
        principal: Principal = Depends(require_principal),
    ):
        del principal
        return await publish_transcript(
            stream_id, payload, resume_token, "transcript.partial"
        )

    @router.post("/{stream_id}/final")
    async def publish_final(
        stream_id: str,
        payload: SpeechTranscriptRequest,
        resume_token: str | None = Header(None, alias="X-VoxAgent-Stream-Token"),
        principal: Principal = Depends(require_principal),
    ):
        del principal
        return await publish_transcript(
            stream_id, payload, resume_token, "transcript.final"
        )

    @router.post("/{stream_id}/cancel")
    async def cancel_stream(
        stream_id: str,
        payload: CancelSpeechStreamRequest,
        resume_token: str | None = Header(None, alias="X-VoxAgent-Stream-Token"),
        principal: Principal = Depends(require_principal),
    ):
        token = _require_token(resume_token)
        event = _call(
            lambda: audio_store.terminate(
                stream_id,
                lambda: broker.cancel(
                    stream_id,
                    token,
                    reason=payload.reason,
                    cancelled_by=payload.cancelled_by,
                    idempotency_key=payload.idempotency_key,
                ),
            )
        )
        processor.clear(stream_id)
        context.voice_audit_log.record(
            "speech.stream.cancelled",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            target_id=stream_id,
            metadata={
                "reason": payload.reason,
                "cancelled_by": payload.cancelled_by,
                "sequence": event["sequence"],
            },
        )
        return event

    @router.post("/{stream_id}/complete")
    async def complete_stream(
        stream_id: str,
        payload: CompleteSpeechStreamRequest,
        resume_token: str | None = Header(None, alias="X-VoxAgent-Stream-Token"),
        principal: Principal = Depends(require_principal),
    ):
        token = _require_token(resume_token)
        snapshot = _call(lambda: audio_store.seal(stream_id, token))
        try:
            if snapshot is not None:
                await asyncio.to_thread(
                    processor.publish_final,
                    snapshot,
                    token,
                )
            event = _call(
                lambda: broker.complete(
                    stream_id,
                    token,
                    reason=payload.reason,
                    idempotency_key=payload.idempotency_key,
                )
            )
        except SpeechAsrProviderError:
            _record_asr_failure(
                context,
                audio_store,
                processor,
                stream_id,
                token,
                principal,
            )
            raise _provider_failure_response() from None
        except Exception as exc:
            _call_raise(exc)
        finally:
            audio_store.discard(stream_id)
            processor.clear(stream_id)
        context.voice_audit_log.record(
            "speech.stream.completed",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            target_id=stream_id,
            metadata={"reason": payload.reason, "sequence": event["sequence"]},
        )
        return event

    return router


def _record_asr_failure(
    context: AppContext,
    audio_store: SpeechAudioChunkStore,
    processor,
    stream_id: str,
    resume_token: str,
    principal: Principal,
) -> None:
    event = _call(
        lambda: audio_store.terminate(
            stream_id,
            lambda: context.speech_stream_broker.record_error(
                stream_id,
                resume_token,
                code="speech_asr_provider_error",
                message="speech recognition provider failed",
                retriable=False,
                idempotency_key=f"asr-provider-error-{stream_id}",
            ),
        )
    )
    processor.clear(stream_id)
    context.voice_audit_log.record(
        "speech.stream.error",
        actor_id=principal.user_id,
        workspace_id=principal.org_id,
        target_id=stream_id,
        metadata={
            "code": "speech_asr_provider_error",
            "retriable": False,
            "sequence": event["sequence"],
        },
    )


def _provider_failure_response() -> HTTPException:
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        {
            "code": "speech_asr_provider_error",
            "message": "speech recognition provider failed",
        },
    )


def _call_raise(exc: Exception):
    def operation():
        raise exc

    return _call(operation)


def _require_token(value: str | None) -> str:
    if value is None or not value.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "speech stream token required")
    return value


def _required_header_integer(value: str | None, name: str) -> int:
    if value is None or not value.isascii() or not value.isdecimal():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{name} must be an integer")
    return int(value)


async def _read_bounded_body(request: Request, max_bytes: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "speech audio chunk exceeds the byte limit",
            )
        body.extend(chunk)
    if not body:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "speech audio chunk is empty",
        )
    return bytes(body)


def _call(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except HTTPException:
        raise
    except StreamNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except StreamCapacityExceeded as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            str(exc),
            headers={"Retry-After": "60"},
        ) from exc
    except InvalidResumeToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except AudioChunkLimitExceeded as exc:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            str(exc),
        ) from exc
    except ReplayGap as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "speech_replay_gap",
                "earliestSequence": exc.earliest_sequence,
                "nextSequence": exc.next_sequence,
            },
        ) from exc
    except (InvalidCursor, RevisionConflict, StreamTerminal, AudioChunkConflict) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except SpeechAudioError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SpeechStreamError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
