"""Durable, bounded retention sweeps for voice-memory data."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from .policy_store import PolicyStore
from .retention_policy import RetentionPolicy
from .voice_audit import VoiceAuditLog

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RetentionSweeper:
    """Preview or delete expired voice data according to a retention policy.

    Audio files are only unlinked when their resolved path is contained by an
    explicitly allowed root. A failed or refused unlink leaves the matching
    database record intact so a later sweep can safely retry it.
    """

    def __init__(
        self,
        *,
        repository: Any,
        policy: RetentionPolicy,
        audit_log: VoiceAuditLog,
        state_store: PolicyStore,
        allowed_audio_dirs: Sequence[Path | str],
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.repository = repository
        self.policy = policy
        self.audit_log = audit_log
        self.state_store = state_store
        self.allowed_audio_dirs = tuple(
            Path(root).expanduser().resolve() for root in allowed_audio_dirs
        )
        self.now = now
        self._run_lock = Lock()

    def run(self, *, dry_run: bool, actor_id: str | None = None) -> dict:
        with self._run_lock:
            return self._run(dry_run=dry_run, actor_id=actor_id)

    def _run(self, *, dry_run: bool, actor_id: str | None = None) -> dict:
        started = self._aware_now()
        effective = self.policy.effective()
        cutoffs = {
            "audio": (
                started - timedelta(days=effective["audioRetentionDays"])
            ).isoformat(),
            "transcripts": (
                started - timedelta(days=effective["transcriptRetentionDays"])
            ).isoformat(),
            "memory": (
                started - timedelta(days=effective["memoryRetentionDays"])
            ).isoformat(),
        }
        candidates = self.repository.retention_candidates(
            audio_before=cutoffs["audio"],
            transcript_before=cutoffs["transcripts"],
            memory_before=cutoffs["memory"],
        )
        result = {
            "id": f"sweep_{uuid4().hex[:12]}",
            "dryRun": dry_run,
            "startedAt": started.isoformat(),
            "completedAt": started.isoformat(),
            "cutoffs": cutoffs,
            "candidates": {
                "audio": len(candidates["audio"]),
                "transcripts": len(candidates["transcripts"]),
                "memory": len(candidates["memory"]),
            },
            "deleted": {"audio": 0, "transcripts": 0, "memory": 0},
            "failures": [],
        }

        if not dry_run:
            audio_session_ids = self._delete_audio_files(
                candidates["audio"], result["failures"]
            )
            result["deleted"] = self.repository.delete_retention_candidates(
                audio_session_ids=audio_session_ids,
                transcript_session_ids=candidates["transcripts"],
                memory_node_ids=candidates["memory"],
            )

        result["completedAt"] = self._aware_now().isoformat()
        self.state_store.record_retention_sweep(result)
        self.audit_log.record(
            (
                "admin.retention-sweep.previewed"
                if dry_run
                else "admin.retention-sweep.executed"
            ),
            actor_id=actor_id,
            metadata={
                "sweepId": result["id"],
                "candidates": result["candidates"],
                "deleted": result["deleted"],
                "failureCount": len(result["failures"]),
            },
        )
        return result

    def _delete_audio_files(self, candidates: list[dict], failures: list[dict]) -> list[str]:
        deleted_session_ids: list[str] = []
        for candidate in candidates:
            session_id = candidate["sessionId"]
            path = Path(candidate["path"]).expanduser()
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                failures.append(
                    {"code": "AUDIO_PATH_INVALID", "sessionId": session_id}
                )
                continue
            if not self._path_is_allowed(resolved):
                failures.append(
                    {
                        "code": "AUDIO_PATH_OUTSIDE_ALLOWED_ROOT",
                        "sessionId": session_id,
                    }
                )
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                failures.append(
                    {
                        "code": "AUDIO_DELETE_FAILED",
                        "sessionId": session_id,
                        "message": str(exc),
                    }
                )
                continue
            deleted_session_ids.append(session_id)
        return deleted_session_ids

    def _path_is_allowed(self, path: Path) -> bool:
        return any(path.is_relative_to(root) for root in self.allowed_audio_dirs)

    def _aware_now(self) -> datetime:
        value = self.now()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class RetentionScheduler:
    """Run automatic sweeps while the application lifespan is active."""

    def __init__(self, sweeper: RetentionSweeper, *, interval_seconds: float = 86_400) -> None:
        self.sweeper = sweeper
        self.interval_seconds = max(1.0, interval_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="retention-sweeper")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            if self.sweeper.policy.effective()["autoDeleteEnabled"]:
                try:
                    await asyncio.to_thread(
                        self.sweeper.run,
                        dry_run=False,
                        actor_id="system:retention-scheduler",
                    )
                except Exception as exc:  # keep the daily scheduler alive
                    logger.exception("automatic retention sweep failed")
                    self.sweeper.audit_log.record(
                        "admin.retention-sweep.failed",
                        actor_id="system:retention-scheduler",
                        metadata={"errorType": type(exc).__name__},
                    )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue
