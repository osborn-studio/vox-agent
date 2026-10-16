"""Privacy-gated realtime translation over cited speech segments."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, RLock
from typing import Callable, Protocol
from uuid import uuid4

TRANSLATION_EVENT_SCHEMA = "voxagent.translation.event"
TRANSLATION_EVENT_SCHEMA_VERSION = 1

_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_FAILURE_CODES = {
    "REMOTE_DISABLED",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_TIMEOUT",
    "INVALID_RESPONSE",
}


class TranslationError(ValueError):
    """Base translation contract error."""


class TranslationIdempotencyConflict(TranslationError):
    """Raised when one idempotency key is reused for different content."""


class TranslationProviderError(RuntimeError):
    """A provider failure that is safe to expose as a translation event."""

    def __init__(self, code: str, message: str, *, retriable: bool) -> None:
        if code not in _FAILURE_CODES - {"REMOTE_DISABLED"}:
            raise ValueError(f"unsupported translation failure code: {code}")
        if not isinstance(retriable, bool):
            raise ValueError("retriable must be a boolean")
        self.code = code
        self.safe_message = _required_text(message, "message", max_length=512)
        self.retriable = retriable
        super().__init__(self.safe_message)


@dataclass(frozen=True, slots=True)
class TranslationResult:
    text: str
    confidence: float


class TranslatorAdapter(Protocol):
    name: str
    model: str
    is_remote: bool

    def translate(
        self,
        *,
        source_text: str,
        source_language: str,
        target_language: str,
        glossary: list[dict],
    ) -> TranslationResult:
        """Translate source text without retaining it beyond the request."""
        ...


class TranslationPipeline:
    """Translate bounded transcript segments with consent and idempotency."""

    def __init__(
        self,
        adapter: TranslatorAdapter,
        *,
        max_cached_requests: int = 1_000,
        clock_ms: Callable[[], float] | None = None,
    ) -> None:
        if max_cached_requests < 1:
            raise ValueError("max_cached_requests must be positive")
        self.adapter = adapter
        self.max_cached_requests = max_cached_requests
        self._clock_ms = clock_ms or (lambda: time.monotonic() * 1_000)
        self._cache: OrderedDict[str, tuple[str, dict]] = OrderedDict()
        self._inflight: dict[str, tuple[str, Event]] = {}
        self._lock = RLock()

    def translate(
        self,
        *,
        source_language: str,
        target_language: str,
        segments: list[dict],
        glossary: list[dict] | None,
        allow_remote: bool,
        idempotency_key: str,
    ) -> dict:
        request = _validated_request(
            source_language=source_language,
            target_language=target_language,
            segments=segments,
            glossary=[] if glossary is None else glossary,
            allow_remote=allow_remote,
            idempotency_key=idempotency_key,
        )
        fingerprint = _fingerprint(request)

        while True:
            with self._lock:
                cached = self._cache.get(request["idempotencyKey"])
                if cached is not None:
                    cached_fingerprint, event = cached
                    if cached_fingerprint != fingerprint:
                        raise TranslationIdempotencyConflict(
                            "idempotency_key was already used for different content"
                        )
                    self._cache.move_to_end(request["idempotencyKey"])
                    return event
                in_flight = self._inflight.get(request["idempotencyKey"])
                if in_flight is None:
                    ready = Event()
                    self._inflight[request["idempotencyKey"]] = (fingerprint, ready)
                    break
                in_flight_fingerprint, ready = in_flight
                if in_flight_fingerprint != fingerprint:
                    raise TranslationIdempotencyConflict(
                        "idempotency_key is in use for different content"
                    )
            ready.wait()

        try:
            event = self._translate_uncached(request)
            if _is_cacheable(event):
                with self._lock:
                    self._cache[request["idempotencyKey"]] = (fingerprint, event)
                    self._cache.move_to_end(request["idempotencyKey"])
                    while len(self._cache) > self.max_cached_requests:
                        self._cache.popitem(last=False)
            return event
        finally:
            with self._lock:
                in_flight = self._inflight.pop(request["idempotencyKey"], None)
                if in_flight is not None:
                    in_flight[1].set()

    def _translate_uncached(self, request: dict) -> dict:
        started_ms = self._clock_ms()
        citations = [_citation(segment) for segment in request["segments"]]
        source_text = "\n".join(segment["text"] for segment in request["segments"])
        glossary_hits = [
            entry for entry in request["glossary"] if entry["sourceText"] in source_text
        ]

        if request["sourceLanguage"] == request["targetLanguage"]:
            translated = source_text
            for entry in glossary_hits:
                translated = translated.replace(entry["sourceText"], entry["targetText"])
            return _final_event(
                request=request,
                citations=citations,
                text=translated,
                confidence=min(segment["languageConfidence"] for segment in request["segments"]),
                provider="identity",
                model="identity-v1",
                glossary_hits=glossary_hits,
                latency_ms=_elapsed_ms(started_ms, self._clock_ms()),
            )

        if self.adapter.is_remote and not request["allowRemote"]:
            return _failed_event(
                request=request,
                citations=citations,
                code="REMOTE_DISABLED",
                message="Remote translation requires explicit consent.",
                retriable=False,
                latency_ms=_elapsed_ms(started_ms, self._clock_ms()),
            )

        try:
            result = self.adapter.translate(
                source_text=source_text,
                source_language=request["sourceLanguage"],
                target_language=request["targetLanguage"],
                glossary=request["glossary"],
            )
            translated_text = _required_text(result.text, "translation text", max_length=32_000)
            confidence = _confidence(result.confidence, "translation confidence")
            if any(entry["targetText"] not in translated_text for entry in glossary_hits):
                raise TranslationProviderError(
                    "INVALID_RESPONSE",
                    "Translation did not preserve the requested glossary terms.",
                    retriable=True,
                )
        except TranslationProviderError as exc:
            return _failed_event(
                request=request,
                citations=citations,
                code=exc.code,
                message=exc.safe_message,
                retriable=exc.retriable,
                latency_ms=_elapsed_ms(started_ms, self._clock_ms()),
            )
        except TimeoutError:
            return _failed_event(
                request=request,
                citations=citations,
                code="PROVIDER_TIMEOUT",
                message="Translation provider timed out.",
                retriable=True,
                latency_ms=_elapsed_ms(started_ms, self._clock_ms()),
            )
        except Exception:
            return _failed_event(
                request=request,
                citations=citations,
                code="PROVIDER_UNAVAILABLE",
                message="Translation provider is unavailable.",
                retriable=True,
                latency_ms=_elapsed_ms(started_ms, self._clock_ms()),
            )

        return _final_event(
            request=request,
            citations=citations,
            text=translated_text,
            confidence=confidence,
            provider=_required_text(self.adapter.name, "provider", max_length=128),
            model=_required_text(self.adapter.model, "model", max_length=256),
            glossary_hits=glossary_hits,
            latency_ms=_elapsed_ms(started_ms, self._clock_ms()),
        )


def _validated_request(
    *,
    source_language: object,
    target_language: object,
    segments: object,
    glossary: object,
    allow_remote: object,
    idempotency_key: object,
) -> dict:
    source = _language(source_language)
    target = _language(target_language)
    if not isinstance(allow_remote, bool):
        raise TranslationError("allow_remote must be a boolean")
    key = _required_text(idempotency_key, "idempotency_key", max_length=160)
    if not isinstance(segments, list) or not 1 <= len(segments) <= 8:
        raise TranslationError("segments must contain between 1 and 8 items")
    normalized_segments = [_source_segment(segment) for segment in segments]
    if sum(len(segment["text"]) for segment in normalized_segments) > 32_000:
        raise TranslationError("combined source text exceeds 32000 characters")
    if not isinstance(glossary, list) or len(glossary) > 128:
        raise TranslationError("glossary must contain at most 128 items")
    normalized_glossary = [_glossary_entry(entry) for entry in glossary]
    return {
        "sourceLanguage": source,
        "targetLanguage": target,
        "segments": normalized_segments,
        "glossary": normalized_glossary,
        "allowRemote": allow_remote,
        "idempotencyKey": key,
    }


def _source_segment(value: object) -> dict:
    if not isinstance(value, dict):
        raise TranslationError("translation segment must be an object")
    expected = {
        "streamId",
        "segmentId",
        "revision",
        "text",
        "languageConfidence",
        "audioStartMs",
        "audioEndMs",
    }
    if set(value) != expected:
        raise TranslationError("translation segment fields do not match the contract")
    revision = _positive_integer(value["revision"], "revision")
    audio_start_ms = _nonnegative_integer(value["audioStartMs"], "audioStartMs")
    audio_end_ms = _nonnegative_integer(value["audioEndMs"], "audioEndMs")
    if audio_end_ms < audio_start_ms:
        raise TranslationError("audioEndMs must be greater than or equal to audioStartMs")
    return {
        "streamId": _required_text(value["streamId"], "streamId", max_length=128),
        "segmentId": _required_text(value["segmentId"], "segmentId", max_length=128),
        "revision": revision,
        "text": _required_text(value["text"], "text", max_length=32_000, strip=False),
        "languageConfidence": _confidence(
            value["languageConfidence"], "languageConfidence"
        ),
        "audioStartMs": audio_start_ms,
        "audioEndMs": audio_end_ms,
    }


def _glossary_entry(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"sourceText", "targetText"}:
        raise TranslationError("glossary entry fields do not match the contract")
    return {
        "sourceText": _required_text(value["sourceText"], "sourceText", max_length=256),
        "targetText": _required_text(value["targetText"], "targetText", max_length=256),
    }


def _citation(segment: dict) -> dict:
    return {
        key: segment[key]
        for key in ("streamId", "segmentId", "revision", "audioStartMs", "audioEndMs")
    }


def _event_base(*, latency_ms: int) -> dict:
    return {
        "schema": TRANSLATION_EVENT_SCHEMA,
        "schemaVersion": TRANSLATION_EVENT_SCHEMA_VERSION,
        "eventId": f"translation_event_{uuid4().hex[:16]}",
        "translationId": f"translation_{uuid4().hex[:16]}",
        "emittedAt": datetime.now(timezone.utc).isoformat(),
        "latencyMs": latency_ms,
    }


def _final_event(
    *,
    request: dict,
    citations: list[dict],
    text: str,
    confidence: float,
    provider: str,
    model: str,
    glossary_hits: list[dict],
    latency_ms: int,
) -> dict:
    return {
        **_event_base(latency_ms=latency_ms),
        "type": "translation.final",
        "payload": {
            "sourceLanguage": request["sourceLanguage"],
            "targetLanguage": request["targetLanguage"],
            "text": text,
            "confidence": confidence,
            "provider": provider,
            "model": model,
            "sourceSegments": citations,
            "glossaryHits": glossary_hits,
        },
    }


def _failed_event(
    *,
    request: dict,
    citations: list[dict],
    code: str,
    message: str,
    retriable: bool,
    latency_ms: int,
) -> dict:
    return {
        **_event_base(latency_ms=latency_ms),
        "type": "translation.failed",
        "payload": {
            "sourceLanguage": request["sourceLanguage"],
            "targetLanguage": request["targetLanguage"],
            "code": code,
            "message": message,
            "retriable": retriable,
            "sourceSegments": citations,
        },
    }


def _fingerprint(request: dict) -> str:
    encoded = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_text(
    value: object,
    field_name: str,
    *,
    max_length: int,
    strip: bool = True,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TranslationError(f"{field_name} must be a non-empty string")
    normalized = value.strip() if strip else value
    if len(normalized) > max_length:
        raise TranslationError(f"{field_name} exceeds {max_length} characters")
    return normalized


def _language(value: object) -> str:
    if not isinstance(value, str) or not _LANGUAGE_RE.fullmatch(value):
        raise TranslationError("language must be a BCP 47 tag")
    return value


def _confidence(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TranslationError(f"{field_name} must be numeric")
    confidence = float(value)
    if not 0 <= confidence <= 1:
        raise TranslationError(f"{field_name} must be between 0 and 1")
    return confidence


def _positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TranslationError(f"{field_name} must be a positive integer")
    return value


def _nonnegative_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TranslationError(f"{field_name} must be a non-negative integer")
    return value


def _elapsed_ms(started_ms: float, finished_ms: float) -> int:
    return max(0, int(finished_ms - started_ms))


def _is_cacheable(event: dict) -> bool:
    return event["type"] == "translation.final" or not event["payload"]["retriable"]
