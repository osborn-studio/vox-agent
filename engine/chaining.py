"""Agent Chaining — pipe output from one agent to another.

Enables multi-agent workflows:
  claude-code (write code) → claude-code (review) → claude-code (test)

Each step's output becomes the next step's input context.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .scheduler import TaskScheduler
from .store import StoreManager
from .events import EventBus

logger = logging.getLogger(__name__)


@dataclass
class ChainStep:
    agent_id: str
    prompt_template: str  # {input} replaced with previous output
    provider: str = "claude"
    profile: str = ""


@dataclass
class ChainResult:
    chain_id: str
    steps_completed: int
    steps_total: int
    final_output: str
    task_ids: list[str]
    status: str  # "completed" | "failed" | "cancelled"
    error: str = ""


class AgentChain:
    """Execute a sequence of agent tasks where each builds on the previous."""

    def __init__(
        self,
        scheduler: TaskScheduler,
        store: StoreManager,
        event_bus: EventBus,
    ) -> None:
        self.scheduler = scheduler
        self.store = store
        self.event_bus = event_bus

    async def execute(
        self,
        chain_id: str,
        steps: list[ChainStep],
        initial_input: str,
    ) -> ChainResult:
        """Execute a chain of agent tasks sequentially."""
        task_ids: list[str] = []
        current_input = initial_input
        last_output = ""

        await self.event_bus.emit("chain:started", {
            "chain_id": chain_id,
            "steps": len(steps),
        })

        for i, step in enumerate(steps):
            prompt = step.prompt_template.replace("{input}", current_input)

            logger.info(
                "chain %s step %d/%d: %s",
                chain_id, i + 1, len(steps), step.agent_id,
            )

            try:
                task = await self.scheduler.create_task(
                    agent_id=step.agent_id,
                    title=f"Chain {chain_id} step {i + 1}/{len(steps)}",
                    prompt=prompt,
                    provider=step.provider,
                )
                task_ids.append(task.id)

                # Wait for completion
                output = await self._wait_for_task(task.id, timeout=600)

                if output is None:
                    return ChainResult(
                        chain_id=chain_id,
                        steps_completed=i,
                        steps_total=len(steps),
                        final_output=last_output,
                        task_ids=task_ids,
                        status="failed",
                        error=f"step {i + 1} failed or timed out",
                    )

                current_input = output
                last_output = output

                await self.event_bus.emit("chain:step_completed", {
                    "chain_id": chain_id,
                    "step": i + 1,
                    "total": len(steps),
                    "task_id": task.id,
                })

            except Exception as e:
                logger.exception("chain %s step %d failed", chain_id, i + 1)
                return ChainResult(
                    chain_id=chain_id,
                    steps_completed=i,
                    steps_total=len(steps),
                    final_output=last_output,
                    task_ids=task_ids,
                    status="failed",
                    error=str(e),
                )

        await self.event_bus.emit("chain:completed", {
            "chain_id": chain_id,
            "steps_completed": len(steps),
        })

        return ChainResult(
            chain_id=chain_id,
            steps_completed=len(steps),
            steps_total=len(steps),
            final_output=last_output,
            task_ids=task_ids,
            status="completed",
        )

    async def _wait_for_task(self, task_id: str, timeout: float = 600) -> str | None:
        """Wait for a task to complete and return its output."""
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            task = self.store.get_task(task_id)
            if not task:
                return None

            status = task["status"]
            if status == "completed":
                # Get the last text message as output
                msgs = self.store.list_task_messages(task_id)
                text_msgs = [m for m in msgs if m["type"] == "text"]
                if text_msgs:
                    return text_msgs[-1].get("content", "")
                return task.get("result", "")
            elif status in ("failed", "cancelled"):
                return None

            await asyncio.sleep(1.0)

        return None  # timeout
