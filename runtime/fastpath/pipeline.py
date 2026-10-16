"""Fast Path pipeline — the critical low-latency chain.

Flow:
  audio → STT → dictionary correction → return display_text

Then emit event to slow path background queue.
Target: P50 < 500ms, P95 < 1.0s (excluding STT network time)
"""

from __future__ import annotations

import time

import structlog

from runtime.fastpath.corrector import Corrector
from runtime.fastpath.stt import transcribe
from runtime.models.config import VoxLogConfig
from runtime.models.events import (
    ArchiveStatus,
    ExportStatus,
    FastPathResult,
    FastPathStatus,
    OutputMode,
    RecordingMode,
    TargetRiskLevel,
    VoiceEvent,
)

logger = structlog.get_logger()


async def fast_path(
    audio: bytes,
    config: VoxLogConfig,
    corrector: Corrector,
    *,
    source: str = "desktop",
    agent: str = "",
    target_app: str = "",
    session_id: str = "",
    recording_mode: RecordingMode = RecordingMode.NORMAL,
    stt_override: str | None = None,
    role: str = "me",
) -> tuple[FastPathResult, VoiceEvent]:
    """Execute fast path. Returns result for immediate UI + event for slow path."""
    start = time.monotonic()

    # STT
    stt_result = await transcribe(audio, config, override=stt_override)

    # Lightweight correction (<1ms)
    display_text, dictionary_applied = corrector.correct_with_trace(stt_result.text)

    total_ms = int((time.monotonic() - start) * 1000)
    confidence = 0.9 if stt_result.text.strip() else 0.0
    target_risk_level = TargetRiskLevel.HIGH if "terminal" in target_app.lower() else TargetRiskLevel.LOW
    needs_review = target_risk_level == TargetRiskLevel.HIGH and confidence < 0.95
    should_autopaste = target_risk_level != TargetRiskLevel.HIGH

    # Build result for immediate return
    result = FastPathResult(
        id="",  # will be set from event
        status=FastPathStatus.NEEDS_REVIEW if needs_review else FastPathStatus.OK,
        display_text=display_text,
        raw_text=stt_result.text,
        stt_provider=stt_result.provider,
        stt_model=stt_result.model,
        target_app=target_app,
        target_risk_level=target_risk_level,
        should_autopaste=should_autopaste,
        needs_review=needs_review,
        confidence=confidence,
        dictionary_applied=dictionary_applied,
        latency_ms=total_ms,
    )

    # Build full event for slow path
    from runtime.audio import get_duration_seconds
    duration_ms = int(get_duration_seconds(audio) * 1000)

    event = VoiceEvent(
        source=source,
        env=config.active_profile,
        agent=agent,
        target_app=target_app,
        target_risk_level=target_risk_level,
        raw_text=stt_result.text,
        display_text=display_text,
        polished_text="",  # slow path will fill this
        audio_duration_ms=duration_ms,
        stt_provider=stt_result.provider,
        stt_model=stt_result.model,
        latency_stt_ms=stt_result.latency_ms,
        latency_total_fast_ms=total_ms,
        recording_mode=recording_mode,
        output_mode=OutputMode.PASTE if should_autopaste else OutputMode.PREVIEW_ONLY,
        confidence=confidence,
        archive_status=ArchiveStatus.QUEUED,
        export_status=ExportStatus.PENDING if recording_mode == RecordingMode.NORMAL else ExportStatus.SKIP,
        session_id=session_id,
        role=role,
    )

    result.id = event.id

    logger.info("fastpath.done", ms=total_ms, stt_ms=stt_result.latency_ms, provider=stt_result.provider)
    return result, event
