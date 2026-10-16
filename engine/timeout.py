"""Task Timeout — automatically fail tasks that exceed their time limit.

Monitors running tasks and forces cancellation when they exceed
the configured timeout (default 2 hours for CLI, 5 min for API).
"""

from __future__ import annotations

import asyncio
import logging

from .store import StoreManager
from .events import EventBus

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 7200  # 2 hours
API_TIMEOUT = 300  # 5 minutes


class TimeoutManager:
    """Monitor and enforce task execution timeouts."""

    def __init__(
        self,
        store: StoreManager,
        event_bus: EventBus,
        default_timeout: float = DEFAULT_TIMEOUT,
        check_interval: float = 30.0,
    ) -> None:
        self.store = store
        self.event_bus = event_bus
        self.default_timeout = default_timeout
        self.check_interval = check_interval
        self._task: asyncio.Task | None = None
        self._custom_timeouts: dict[str, float] = {}  # task_id → timeout_seconds

    def set_timeout(self, task_id: str, timeout_seconds: float) -> None:
        """Set a custom timeout for a specific task."""
        self._custom_timeouts[task_id] = timeout_seconds

    def start(self) -> None:
        """Start the timeout monitoring loop."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._monitor_loop())
            logger.info("timeout monitor started (check every %.0fs)", self.check_interval)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def check_timeouts(self) -> list[str]:
        """Check for timed-out tasks. Returns list of timed-out task IDs."""
        self.store._ensure_connected()
        running = self.store.list_tasks(status="running")
        timed_out = []

        for task in running:
            started = task.get("started_at")
            if not started:
                continue

            from datetime import datetime, timezone
            try:
                start_time = datetime.fromisoformat(started)
                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            except (ValueError, TypeError):
                continue

            timeout = self._custom_timeouts.get(task["id"], self.default_timeout)
            if elapsed > timeout:
                timed_out.append(task["id"])
                logger.warning(
                    "task %s timed out (%.0fs > %.0fs limit)",
                    task["id"][:8], elapsed, timeout,
                )

                # Mark as failed
                from .models import TaskStatus
                self.store.update_task_status(
                    task["id"], TaskStatus.FAILED,
                    error=f"timeout: {elapsed:.0f}s exceeded {timeout:.0f}s limit",
                )

                await self.event_bus.emit("task:failed", {
                    "task_id": task["id"],
                    "error": f"timeout after {elapsed:.0f}s",
                    "status": "failed",
                })

                # Clean up custom timeout
                self._custom_timeouts.pop(task["id"], None)

        return timed_out

    async def _monitor_loop(self) -> None:
        """Background loop that checks for timeouts periodically."""
        while True:
            try:
                await asyncio.sleep(self.check_interval)
                timed_out = await self.check_timeouts()
                if timed_out:
                    logger.info("timeout check: %d tasks timed out", len(timed_out))
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("timeout monitor error")
