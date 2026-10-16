"""Recurring Tasks — schedule tasks to run on intervals or at specific times.

Supports:
- Interval-based: "run tests every 30 minutes"
- Daily schedule: "export to Obsidian at 2am"
- On-demand triggers: "when agent comes online, run health check"
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)

DEFAULT_SCHEDULES_PATH = resolve_vox_agent_data_dir() / "schedules.json"


@dataclass
class RecurringSchedule:
    id: str
    name: str
    agent_id: str
    prompt: str
    interval_minutes: int = 0      # 0 = not interval-based
    daily_at: str = ""             # HH:MM format, "" = not daily
    provider: str = "claude"
    enabled: bool = True
    last_run: str = ""             # ISO timestamp
    run_count: int = 0
    max_runs: int = 0              # 0 = unlimited


class RecurringScheduler:
    """Manage and execute recurring task schedules."""

    def __init__(self, schedules_path: Path | None = None) -> None:
        self.path = schedules_path or (resolve_vox_agent_data_dir() / "schedules.json")
        self._schedules: dict[str, RecurringSchedule] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._on_trigger: Callable[[RecurringSchedule], Awaitable[None]] | None = None
        self._load()

    def set_trigger(self, callback: Callable[[RecurringSchedule], Awaitable[None]]) -> None:
        """Set the callback for when a schedule triggers."""
        self._on_trigger = callback

    # -- CRUD ----------------------------------------------------------------

    def add(self, schedule: RecurringSchedule) -> None:
        self._schedules[schedule.id] = schedule
        self._save()
        logger.info("schedule added: %s (%s)", schedule.name, schedule.id)

    def remove(self, schedule_id: str) -> bool:
        if schedule_id in self._schedules:
            del self._schedules[schedule_id]
            self._save()
            # Cancel running task
            if schedule_id in self._tasks:
                self._tasks[schedule_id].cancel()
                del self._tasks[schedule_id]
            return True
        return False

    def get(self, schedule_id: str) -> RecurringSchedule | None:
        return self._schedules.get(schedule_id)

    def list_all(self) -> list[RecurringSchedule]:
        return list(self._schedules.values())

    def enable(self, schedule_id: str) -> None:
        if s := self._schedules.get(schedule_id):
            s.enabled = True
            self._save()

    def disable(self, schedule_id: str) -> None:
        if s := self._schedules.get(schedule_id):
            s.enabled = False
            self._save()
            if schedule_id in self._tasks:
                self._tasks[schedule_id].cancel()
                del self._tasks[schedule_id]

    # -- Execution -----------------------------------------------------------

    def start_all(self) -> int:
        """Start all enabled interval schedules. Returns count started."""
        started = 0
        for s in self._schedules.values():
            if s.enabled and s.interval_minutes > 0:
                self._start_interval(s)
                started += 1
        logger.info("started %d recurring schedules", started)
        return started

    def stop_all(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    def _start_interval(self, schedule: RecurringSchedule) -> None:
        if schedule.id in self._tasks:
            self._tasks[schedule.id].cancel()

        async def loop():
            while True:
                await asyncio.sleep(schedule.interval_minutes * 60)
                if not schedule.enabled:
                    break
                if schedule.max_runs > 0 and schedule.run_count >= schedule.max_runs:
                    logger.info("schedule %s reached max runs (%d)", schedule.id, schedule.max_runs)
                    break
                await self._execute(schedule)

        self._tasks[schedule.id] = asyncio.create_task(loop())

    async def _execute(self, schedule: RecurringSchedule) -> None:
        """Execute a scheduled task."""
        logger.info("executing schedule: %s", schedule.name)
        schedule.last_run = datetime.now(timezone.utc).isoformat()
        schedule.run_count += 1
        self._save()

        if self._on_trigger:
            try:
                await self._on_trigger(schedule)
            except Exception:
                logger.exception("schedule %s trigger failed", schedule.id)

    async def check_daily(self) -> list[str]:
        """Check if any daily schedules should run now. Returns triggered IDs.

        Call this from a minute-level timer (e.g., every 60 seconds).
        """
        now = datetime.now(timezone.utc)
        current_hm = now.strftime("%H:%M")
        triggered = []

        for s in self._schedules.values():
            if not s.enabled or not s.daily_at:
                continue
            if s.daily_at != current_hm:
                continue
            # Check if already run today
            if s.last_run:
                last = datetime.fromisoformat(s.last_run)
                if last.date() == now.date():
                    continue
            await self._execute(s)
            triggered.append(s.id)

        return triggered

    # -- Persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            for item in data:
                s = RecurringSchedule(**item)
                self._schedules[s.id] = s
        except Exception:
            logger.exception("failed to load schedules from %s", self.path)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(s) for s in self._schedules.values()]
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
