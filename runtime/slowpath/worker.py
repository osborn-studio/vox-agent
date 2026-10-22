"""Slow Path background worker — processes voice events asynchronously.

Runs after fast path returns. Failures here NEVER affect the user's input.

Jobs:
1. Archive raw event to SQLite
2. Optional LLM polish
3. Update search index
4. Queue for export
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

import structlog

from runtime.models.config import VoxLogConfig
from runtime.models.events import ArchiveStatus, RecordingMode, VoiceEvent
from runtime.slowpath.digester import DailyDigester, ProjectDigester, SessionDigester
from runtime.slowpath.enhancer import DigestEnhancer

logger = structlog.get_logger()

# Background queue
_event_queue: deque[VoiceEvent] = deque(maxlen=1000)
_worker_task: asyncio.Task | None = None
_digester = SessionDigester()
_daily_digester = DailyDigester()
_project_digester = ProjectDigester()
_enhancer = DigestEnhancer()


def enqueue(event: VoiceEvent) -> None:
    """Add event to slow path queue. Non-blocking."""
    if event.recording_mode == RecordingMode.EPHEMERAL:
        logger.debug("slowpath.skip_ephemeral", id=event.id)
        return
    _event_queue.append(event)
    logger.debug("slowpath.enqueued", id=event.id, queue_size=len(_event_queue))


async def start_worker(config: VoxLogConfig, archive, polisher=None) -> None:
    """Start the background worker loop."""
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker_loop(config, archive, polisher))
    logger.info("slowpath.worker_started")


async def _worker_loop(config, archive, polisher):
    """Process events from queue forever."""
    while True:
        if _event_queue:
            event = _event_queue.popleft()
            try:
                await _process_event(event, config, archive, polisher)
            except Exception as e:
                logger.error("slowpath.process_fail", id=event.id, error=str(e)[:200])
        else:
            await asyncio.sleep(0.1)  # Poll every 100ms


async def _process_event(event: VoiceEvent, config, archive, polisher):
    """Process one event through the slow path."""
    start = time.monotonic()

    # Step 1: Archive raw event
    try:
        await archive.save_event(event)
        event.archive_status = ArchiveStatus.RAW_ONLY
        logger.debug("slowpath.archived", id=event.id)
    except Exception as e:
        event.archive_status = ArchiveStatus.FAILED
        logger.error("slowpath.archive_fail", id=event.id, error=str(e)[:100])
        return  # Don't proceed if archive fails

    # Step 2a: Persist an inline polish result so history sees it. The fast
    # path sets polished_text when the caller asked for polish=1 and the
    # provider answered; the worker must not call the provider again.
    if event.polished_text:
        try:
            await archive.update_polish(event.id, event.polished_text)
            event.archive_status = ArchiveStatus.POLISHED
            logger.debug("slowpath.polished_inline", id=event.id)
        except Exception as e:
            logger.warning("slowpath.polish_persist_fail", id=event.id, error=str(e)[:100])

    # Step 2b: Optional LLM polish (only for normal mode, and only when
    # inline polish did not already run for this event)
    if (
        event.recording_mode == RecordingMode.NORMAL
        and polisher
        and event.raw_text
        and not event.polished_text  # inline polish already ran for this event
    ):
        try:
            polished = await asyncio.wait_for(
                polisher.polish(event.raw_text, config),
                timeout=10.0,
            )
            if polished:
                event.polished_text = polished
                event.archive_status = ArchiveStatus.POLISHED
                await archive.update_polish(event.id, polished)
                logger.debug("slowpath.polished", id=event.id)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("slowpath.polish_fail", id=event.id, error=str(e)[:100])
            # Polish failure is fine — raw text is preserved

    # Step 3: Session digest compilation
    try:
        digest = _digester.build(event)
        digest_text = event.polished_text or event.display_text or event.raw_text
        digest = await _enhancer.enhance(digest, digest_text, config)
        await archive.upsert_session_digest(
            session_id=digest["session_id"],
            source_event_id=digest["source_event_id"],
            summary=digest["summary"],
            intent=digest["intent"],
            suggested_tags=digest["suggested_tags"],
            mentioned_entities=digest["mentioned_entities"],
            enhanced=digest.get("enhanced", False),
            enhancer_provider=digest.get("enhancer_provider", "heuristic"),
        )
        logger.debug("slowpath.session_digest", id=event.id, session_id=event.session_id)
    except Exception as e:
        logger.warning("slowpath.digest_fail", id=event.id, error=str(e)[:100])

    # Step 4: Daily digest compilation
    try:
        digest_date = event.created_at.date().isoformat()
        daily_events = await archive.list_events_for_date(digest_date)
        daily_digest = _daily_digester.build(daily_events, date_key=digest_date)
        daily_source_text = "\n".join(
            (item.polished_text or item.display_text or item.raw_text).strip()
            for item in daily_events
            if (item.polished_text or item.display_text or item.raw_text).strip()
        )
        daily_digest = await _enhancer.enhance(daily_digest, daily_source_text, config)
        await archive.upsert_daily_digest(
            digest_date=daily_digest["digest_date"],
            source_event_id=daily_digest["source_event_id"],
            summary=daily_digest["summary"],
            intent=daily_digest["intent"],
            suggested_tags=daily_digest["suggested_tags"],
            mentioned_entities=daily_digest["mentioned_entities"],
            enhanced=daily_digest.get("enhanced", False),
            enhancer_provider=daily_digest.get("enhancer_provider", "heuristic"),
        )
        logger.debug("slowpath.daily_digest", id=event.id, digest_date=digest_date)
    except Exception as e:
        logger.warning("slowpath.daily_digest_fail", id=event.id, error=str(e)[:100])

    # Step 5: Project digest compilation
    try:
        project_key = event.target_app.strip().lower()
        if project_key:
            project_events = await archive.list_events_for_project(project_key)
            project_digest = _project_digester.build(project_events, project_key=project_key)
            project_source_text = "\n".join(
                (item.polished_text or item.display_text or item.raw_text).strip()
                for item in project_events
                if (item.polished_text or item.display_text or item.raw_text).strip()
            )
            project_digest = await _enhancer.enhance(project_digest, project_source_text, config)
            await archive.upsert_project_digest(
                project_key=project_digest["project_key"],
                source_event_id=project_digest["source_event_id"],
                summary=project_digest["summary"],
                intent=project_digest["intent"],
                suggested_tags=project_digest["suggested_tags"],
                mentioned_entities=project_digest["mentioned_entities"],
                enhanced=project_digest.get("enhanced", False),
                enhancer_provider=project_digest.get("enhancer_provider", "heuristic"),
            )
            logger.debug("slowpath.project_digest", id=event.id, project_key=project_key)
    except Exception as e:
        logger.warning("slowpath.project_digest_fail", id=event.id, error=str(e)[:100])

    elapsed = int((time.monotonic() - start) * 1000)
    event.latency_total_slow_ms = elapsed
    logger.info("slowpath.done", id=event.id, ms=elapsed, status=event.archive_status.value)
