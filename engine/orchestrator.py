"""Orchestrator — the central coordinator that ties voice input to agent execution.

This is the "brain" of Vox Agents. It receives user messages (from SwiftUI via API),
classifies intent, and routes to the appropriate handler:

  voice/text → VoiceCommands → IntentParser → route:
    command → execute voice shortcut (profile switch, status, export...)
    record  → store message only
    task    → create Task → dispatch to Agent Engine
    query   → look up task status → respond
    confirm → mark task as accepted
    cancel  → cancel running task
    chat    → forward to agent for conversational reply
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .intent import IntentParser, Intent
from .voice_commands import VoiceCommandParser
from .scheduler import TaskScheduler
from .events import EventBus
from .store import StoreManager
from .models import TaskPriority, ActivityEntry

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorResponse:
    """Response sent back to SwiftUI after processing a message."""
    action: str  # "recorded" | "task_created" | "status" | "confirmed" | "cancelled" | "chat"
    message: str = ""
    task_id: str | None = None
    data: dict[str, Any] | None = None


class Orchestrator:
    """Routes user messages based on classified intent."""

    def __init__(
        self,
        intent_parser: IntentParser,
        scheduler: TaskScheduler,
        event_bus: EventBus,
        store: StoreManager,
        use_terminal_bridge: bool = False,
    ) -> None:
        self.intent = intent_parser
        self.voice_commands = VoiceCommandParser()
        self.scheduler = scheduler
        self.event_bus = event_bus
        self.store = store
        self.use_terminal_bridge = use_terminal_bridge

    async def process_message(
        self,
        text: str,
        agent_id: str | None = None,
        provider: str = "claude",
    ) -> OrchestratorResponse:
        """Process a user message and route to the appropriate handler."""
        # 1. Check voice commands first (fast, regex-based)
        cmd, params = self.voice_commands.parse(text)
        if cmd:
            return await self._handle_voice_command(cmd, params, agent_id)

        # 2. Fall through to intent classification
        result = self.intent.parse(text, agent_id=agent_id)
        logger.info(
            "intent: %s (%.0f%%) for agent=%s text=%s...",
            result.intent.value, result.confidence * 100,
            agent_id, text[:50],
        )

        match result.intent:
            case Intent.RECORD:
                return self._handle_record(text, agent_id)

            case Intent.TASK:
                return await self._handle_task(
                    text, agent_id, result.extracted_title,
                    result.extracted_priority, provider,
                )

            case Intent.QUERY:
                return self._handle_query(agent_id)

            case Intent.CONFIRM:
                return await self._handle_confirm(agent_id)

            case Intent.CANCEL:
                return await self._handle_cancel(agent_id)

            case Intent.CHAT:
                return await self._handle_chat(text, agent_id, provider)

            case _:
                return OrchestratorResponse(
                    action="recorded",
                    message="Message saved.",
                )

    # -- Handlers ------------------------------------------------------------

    def _handle_record(self, text: str, agent_id: str | None) -> OrchestratorResponse:
        self.store.insert_activity(ActivityEntry(
            actor_type="user",
            actor_id="gilbert",
            action="message:recorded",
            details={"text": text[:200], "agent": agent_id or "notes"},
        ))
        return OrchestratorResponse(
            action="recorded",
            message="Noted.",
        )

    async def _handle_task(
        self,
        text: str,
        agent_id: str | None,
        title: str,
        priority: str,
        provider: str,
    ) -> OrchestratorResponse:
        if not agent_id:
            return OrchestratorResponse(
                action="recorded",
                message="No agent selected. Please choose an agent first.",
            )

        # Route based on agent type
        # 1. Claude Code CLI → send to Terminal with cc
        if agent_id == "claude-code" and self.use_terminal_bridge:
            from .terminal_bridge import TerminalBridge
            bridge = TerminalBridge(
                default_dir=str(Path.home() / "Developer/ClaudeCode/01_active/voxAgent")
            )
            result = await bridge.send_to_session("CC1", prompt=text)
            return OrchestratorResponse(
                action="terminal_sent",
                message="Task sent to Claude Code CLI in Terminal.",
                data={"terminal": result, "title": title or text[:50]},
            )

        # 2. OpenClaw → forward to OpenClaw gateway
        if agent_id == "openclaw":
            from .openclaw_bridge import OpenClawBridge as OCBridge
            oc = OCBridge()
            health = await oc.check_health()
            if health:
                result = await oc.send_message(message=text)
                return OrchestratorResponse(
                    action="openclaw_sent",
                    message="Sent to OpenClaw (小龙虾).",
                    data={"openclaw": result},
                )
            else:
                return OrchestratorResponse(
                    action="error",
                    message="OpenClaw is not running. Start it first.",
                )

        # 3. Default: use the engine scheduler (API direct / subprocess)
        priority_map = {
            "low": TaskPriority.LOW,
            "medium": TaskPriority.MEDIUM,
            "high": TaskPriority.HIGH,
            "urgent": TaskPriority.URGENT,
        }

        task = await self.scheduler.create_task(
            agent_id=agent_id,
            title=title or text[:50],
            prompt=text,
            priority=priority_map.get(priority, TaskPriority.MEDIUM),
            provider=provider,
        )

        return OrchestratorResponse(
            action="task_created",
            message=f"Task dispatched to {agent_id}.",
            task_id=task.id,
            data={"title": task.title, "status": task.status.value},
        )

    def _handle_query(self, agent_id: str | None) -> OrchestratorResponse:
        if not agent_id:
            all_tasks = self.store.list_tasks()
            active = [t for t in all_tasks if t["status"] in ("running", "assigned")]
            if not active:
                return OrchestratorResponse(action="status", message="No active tasks.")
            summaries = [f"• {t['title']} ({t['status']})" for t in active[:5]]
            return OrchestratorResponse(
                action="status",
                message=f"{len(active)} active tasks:\n" + "\n".join(summaries),
            )

        tasks = self.store.list_tasks(agent_id=agent_id)
        active = [t for t in tasks if t["status"] in ("running", "assigned")]
        if not active:
            return OrchestratorResponse(
                action="status",
                message=f"No active tasks for {agent_id}.",
            )

        latest = active[0]
        msgs = self.store.list_task_messages(latest["id"])
        last_msg = msgs[-1]["content"] if msgs else "no updates yet"
        return OrchestratorResponse(
            action="status",
            message=f"{latest['title']}: {latest['status']}. Last: {last_msg[:100]}",
            task_id=latest["id"],
        )

    async def _handle_confirm(self, agent_id: str | None) -> OrchestratorResponse:
        if not agent_id:
            return OrchestratorResponse(action="confirmed", message="Nothing to confirm.")

        tasks = self.store.list_tasks(agent_id=agent_id)
        completed = [t for t in tasks if t["status"] == "completed"]
        if not completed:
            return OrchestratorResponse(
                action="confirmed",
                message="No completed tasks to confirm.",
            )

        # Confirm the most recent completed task
        latest = completed[0]
        self.store.insert_activity(ActivityEntry(
            actor_type="user",
            actor_id="gilbert",
            action="task:confirmed",
            target_type="task",
            target_id=latest["id"],
        ))
        return OrchestratorResponse(
            action="confirmed",
            message=f"Confirmed: {latest['title']}",
            task_id=latest["id"],
        )

    async def _handle_cancel(self, agent_id: str | None) -> OrchestratorResponse:
        if not agent_id:
            return OrchestratorResponse(action="cancelled", message="No agent selected.")

        tasks = self.store.list_tasks(agent_id=agent_id)
        active = [t for t in tasks if t["status"] in ("running", "assigned")]
        if not active:
            return OrchestratorResponse(
                action="cancelled",
                message="No active tasks to cancel.",
            )

        latest = active[0]
        ok = await self.scheduler.cancel_task(latest["id"])
        if ok:
            return OrchestratorResponse(
                action="cancelled",
                message=f"Cancelled: {latest['title']}",
                task_id=latest["id"],
            )
        return OrchestratorResponse(
            action="cancelled",
            message="Could not cancel task.",
        )

    async def _handle_chat(
        self, text: str, agent_id: str | None, provider: str
    ) -> OrchestratorResponse:
        # For now, create a low-priority task for conversational responses
        if not agent_id:
            return OrchestratorResponse(action="chat", message="Select an agent to chat with.")

        task = await self.scheduler.create_task(
            agent_id=agent_id,
            title=f"Chat: {text[:40]}",
            prompt=text,
            priority=TaskPriority.LOW,
            provider=provider,
        )
        return OrchestratorResponse(
            action="chat",
            message="Thinking...",
            task_id=task.id,
        )

    # -- Voice Commands ------------------------------------------------------

    async def _handle_voice_command(
        self, cmd: Any, params: dict, agent_id: str | None
    ) -> OrchestratorResponse:
        """Execute a voice shortcut command."""
        match cmd.handler_key:
            case "switch_profile":
                profile = params.get("match", "coding")
                return OrchestratorResponse(
                    action="command",
                    message=f"Switched to {profile} mode.",
                    data={"command": "switch_profile", "profile": profile},
                )
            case "show_status":
                tasks = self.store.list_tasks()
                active = sum(1 for t in tasks if t["status"] in ("running", "assigned"))
                return OrchestratorResponse(
                    action="command",
                    message=f"Engine OK. {active} active tasks, {len(tasks)} total.",
                    data={"command": "show_status", "active": active, "total": len(tasks)},
                )
            case "export_obsidian":
                return OrchestratorResponse(
                    action="command",
                    message="Exporting to Obsidian...",
                    data={"command": "export_obsidian"},
                )
            case "show_costs":
                return OrchestratorResponse(
                    action="command",
                    message="Fetching cost summary...",
                    data={"command": "show_costs"},
                )
            case "cancel_all":
                if not agent_id:
                    return OrchestratorResponse(action="command", message="No agent selected.")
                tasks = self.store.list_tasks(agent_id=agent_id)
                active = [t for t in tasks if t["status"] in ("running", "assigned")]
                cancelled = 0
                for t in active:
                    await self.scheduler.cancel_task(t["id"])
                    cancelled += 1
                return OrchestratorResponse(
                    action="command",
                    message=f"Cancelled {cancelled} tasks for {agent_id}.",
                    data={"command": "cancel_all", "cancelled": cancelled},
                )
            case "show_tasks":
                tasks = self.store.list_tasks(agent_id=agent_id)
                summaries = [f"• {t['title']} ({t['status']})" for t in tasks[:10]]
                msg = "\n".join(summaries) if summaries else "No tasks."
                return OrchestratorResponse(
                    action="command",
                    message=msg,
                    data={"command": "show_tasks", "count": len(tasks)},
                )
            case "show_agents":
                return OrchestratorResponse(
                    action="command",
                    message="Loading agents...",
                    data={"command": "show_agents"},
                )
            case "run_template":
                template_id = params.get("match", "").replace(" ", "-").lower()
                return OrchestratorResponse(
                    action="command",
                    message=f"Using template: {template_id}",
                    data={"command": "run_template", "template_id": template_id},
                )
            case _:
                return OrchestratorResponse(
                    action="command",
                    message=f"Unknown command: {cmd.id}",
                )
