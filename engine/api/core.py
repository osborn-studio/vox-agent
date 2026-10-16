"""Core task, agent, intent, and event-stream routes."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..app_context import AppContext
from ..auth import Principal, require_principal
from ..models import TaskPriority

logger = logging.getLogger(__name__)


class CreateTaskRequest(BaseModel):
    task_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    agent_id: str
    title: str
    prompt: str
    description: str = ""
    priority: int = 1
    trigger_message_id: str | None = None
    source_agent: str | None = None
    provider: str = "claude"


class MessageRequest(BaseModel):
    working_directory: str | None = Field(default=None, strict=True, max_length=4096)
    text: str
    agent_id: str | None = None
    provider: str = "claude"


class IntentRequest(BaseModel):
    text: str
    agent_id: str | None = None


def create_router(context: AppContext) -> APIRouter:
    """Bind core routes to one isolated application context."""

    router = APIRouter(tags=["core"])

    @router.post("/v1/tasks")
    async def create_task(
        req: CreateTaskRequest,
        principal: Principal = Depends(require_principal),
    ):
        if req.task_id:
            existing = context.store.get_task(req.task_id)
            if existing:
                same_request = (
                    existing["agent_id"] == req.agent_id
                    and existing["title"] == req.title
                    and existing["prompt"] == req.prompt
                    and existing["requester_user_id"] == principal.user_id
                    and existing["workspace_id"] == principal.org_id
                )
                if not same_request:
                    raise HTTPException(409, "task_id already belongs to another task")
                return {"task_id": req.task_id, "status": existing["status"]}
        task = await context.scheduler.create_task(
            agent_id=req.agent_id,
            title=req.title,
            prompt=req.prompt,
            description=req.description,
            priority=TaskPriority(req.priority),
            trigger_message_id=req.trigger_message_id,
            source_agent=req.source_agent,
            provider=req.provider,
            task_id=req.task_id,
            requester_user_id=principal.user_id,
            workspace_id=principal.org_id,
        )
        return {"task_id": task.id, "status": task.status.value}

    @router.get("/v1/tasks/{task_id}")
    async def get_task(task_id: str):
        row = context.store.get_task(task_id)
        if not row:
            raise HTTPException(404, "task not found")
        return dict(row)

    @router.get("/v1/tasks/{task_id}/messages")
    async def get_task_messages(task_id: str):
        return context.store.list_task_messages(task_id)

    @router.post("/v1/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        if not await context.scheduler.cancel_task(task_id):
            raise HTTPException(400, "cannot cancel task")
        return {"status": "cancelled"}

    @router.post("/v1/tasks/{task_id}/pause")
    async def pause_task(task_id: str):
        if not await context.scheduler.pause_task(task_id):
            raise HTTPException(400, "cannot pause task")
        return {"status": "blocked"}

    @router.post("/v1/tasks/{task_id}/resume")
    async def resume_task(task_id: str):
        if not await context.scheduler.resume_task(task_id):
            raise HTTPException(400, "cannot resume task")
        return {"status": "running"}

    @router.get("/v1/tasks")
    async def list_tasks(agent_id: str | None = None, status: str | None = None):
        return context.store.list_tasks(agent_id=agent_id, status=status)

    @router.get("/v1/tools")
    async def list_tools():
        return [
            {"name": tool.name, "description": tool.description}
            for tool in context.tool_registry.list_tools()
        ]

    @router.get("/v1/skills")
    async def list_skills():
        return [
            {"name": skill.name, "description": skill.description, "tools": skill.tools}
            for skill in context.skill_loader.load_all()
        ]

    @router.get("/v1/agents")
    async def list_agents():
        return [_agent_response(agent) for agent in context.agent_scanner.scan()]

    @router.get("/v1/agents/{agent_id}")
    async def get_agent(agent_id: str):
        config = context.agent_scanner.get_agent(agent_id)
        if not config:
            raise HTTPException(404, "agent not found")
        return _agent_response(config)

    @router.get("/v1/engine/status")
    async def engine_status():
        return {
            "status": "ok",
            "active_tasks": len(context.agent_engine._processes),
        }

    @router.post("/v1/message")
    async def process_message(
        req: MessageRequest, principal: Principal = Depends(require_principal),
    ):
        if req.working_directory is not None:
            directory = Path(req.working_directory)
            if not directory.is_absolute() or not directory.is_dir():
                raise HTTPException(422, "Choose an existing absolute repository directory")
            directory = directory.resolve()
            if not (directory / ".git").exists() or any(
                part.endswith(".app") for part in directory.parts
            ):
                raise HTTPException(422, "Choose a Git repository root outside an application bundle")
            if req.provider != "claude" or not req.agent_id or not req.text.strip():
                raise HTTPException(422, "Repository execution requires Claude and a selected agent")
            task = await context.scheduler.create_task(
                agent_id=req.agent_id, title=req.text[:50], prompt=req.text,
                provider=req.provider, working_directory=str(directory),
                requester_user_id=principal.user_id, workspace_id=principal.org_id,
            )
            return {"action": "task_created", "message": "", "task_id": task.id, "data": {}}

        result = await context.orchestrator.process_message(
            text=req.text,
            agent_id=req.agent_id,
            provider=req.provider,
        )
        return {
            "action": result.action,
            "message": result.message,
            "task_id": result.task_id,
            "data": result.data,
        }

    @router.post("/v1/intent")
    async def parse_intent(req: IntentRequest):
        result = context.intent_parser.parse(req.text, agent_id=req.agent_id)
        return {
            "intent": result.intent.value,
            "confidence": result.confidence,
            "extracted_title": result.extracted_title,
            "extracted_priority": result.extracted_priority,
            "reasoning": result.reasoning,
        }

    @router.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        queue = context.event_bus.subscribe_ws()

        async def send_events() -> None:
            while True:
                await ws.send_json(await queue.get())

        sender_task = asyncio.create_task(send_events())
        logger.info("ws client connected")
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            logger.info("ws client disconnected")
        finally:
            sender_task.cancel()
            await asyncio.gather(sender_task, return_exceptions=True)
            context.event_bus.unsubscribe_ws(queue)

    return router


def _agent_response(agent) -> dict:
    return {
        "id": agent.id,
        "name": agent.name,
        "emoji": agent.emoji,
        "provider": agent.provider,
        "parent_id": agent.parent_id,
        "external_system": agent.external_system,
        "skills": agent.skills,
        "max_concurrent": agent.max_concurrent,
    }
