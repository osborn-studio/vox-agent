"""Terminal, gateway, webhook, runtime, and scheduling routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..app_context import AppContext
from ..recurring import RecurringSchedule
from ..webhooks import WebhookConfig, WebhookEvent


class TerminalLaunchRequest(BaseModel):
    prompt: str
    working_dir: str | None = None
    new_window: bool = True


class SendToSessionRequest(BaseModel):
    session_id: str = "CC1"
    prompt: str
    working_dir: str | None = None


class OpenClawMessageRequest(BaseModel):
    agent_ref: str = ""
    message: str
    workspace: str = "personal"


class OpenClawTaskRequest(BaseModel):
    title: str
    prompt: str
    agent_ref: str = ""
    workspace: str = "personal"


class WebhookConfigRequest(BaseModel):
    id: str
    name: str
    source: str
    agent_id: str = "claude-code"
    prompt_template: str = "Webhook: {event}"
    secret: str = ""


class HeartbeatRequest(BaseModel):
    agent_id: str
    status: str = "online"
    active_tasks: int = 0


class ScheduleRequest(BaseModel):
    id: str
    name: str
    agent_id: str
    prompt: str
    interval_minutes: int = 0
    daily_at: str = ""
    provider: str = "claude"
    max_runs: int = 0


def create_router(context: AppContext) -> APIRouter:
    """Bind integration routes to one isolated application context."""

    router = APIRouter(tags=["integrations"])
    services = context.api

    @router.post("/v1/terminal/session")
    async def create_cc_session(
        working_dir: str | None = None,
        session_id: str | None = None,
    ):
        return await services.terminal_bridge.create_session(working_dir, session_id)

    @router.post("/v1/terminal/send")
    async def send_to_cc_session(req: SendToSessionRequest):
        return await services.terminal_bridge.send_to_session(
            req.session_id, req.prompt
        )

    @router.get("/v1/terminal/sessions")
    async def list_cc_sessions():
        return services.terminal_bridge.list_sessions()

    @router.delete("/v1/terminal/session/{session_id}")
    async def close_cc_session(session_id: str):
        if services.terminal_bridge.close_session(session_id):
            return {"status": "closed"}
        raise HTTPException(404, "session not found")

    @router.post("/v1/terminal/launch")
    async def launch_terminal(req: TerminalLaunchRequest):
        return await services.terminal_bridge.send_to_session("CC1", req.prompt)

    @router.post("/v1/terminal/interactive")
    async def launch_interactive(working_dir: str | None = None):
        return await services.terminal_bridge.create_session(working_dir)

    @router.get("/v1/openclaw/health")
    async def openclaw_health():
        online = await services.openclaw_gateway.check_health()
        return {
            "online": online,
            "gateway": services.openclaw_gateway.gateway,
        }

    @router.post("/v1/openclaw/chat")
    async def openclaw_chat(req: OpenClawMessageRequest):
        return await services.openclaw_gateway.send_message(
            agent_ref=req.agent_ref,
            message=req.message,
            workspace=req.workspace,
        )

    @router.post("/v1/openclaw/task")
    async def openclaw_task(req: OpenClawTaskRequest):
        return await services.openclaw_gateway.dispatch_task(
            title=req.title,
            prompt=req.prompt,
            agent_ref=req.agent_ref,
            workspace=req.workspace,
        )

    @router.get("/v1/openclaw/agents")
    async def openclaw_agents(workspace: str = "personal"):
        return await services.openclaw_gateway.get_agents(workspace)

    @router.post("/v1/openclaw/open")
    async def openclaw_open_ui():
        return await services.openclaw_gateway.open_web_ui()

    @router.post("/v1/route")
    async def route_task(text: str, preferred_agent: str | None = None):
        result = services.agent_router.route(text, preferred_agent)
        return {
            "agent_id": result.agent_id,
            "confidence": result.confidence,
            "reason": result.reason,
        }

    @router.post("/v1/route/suggest")
    async def suggest_agents_for_task(text: str, top_n: int = 3):
        return [
            {
                "agent_id": suggestion.agent_id,
                "confidence": suggestion.confidence,
                "reason": suggestion.reason,
            }
            for suggestion in services.agent_router.suggest_agents(text, top_n)
        ]

    @router.post("/v1/webhooks")
    async def register_webhook(req: WebhookConfigRequest):
        config = WebhookConfig(
            id=req.id,
            name=req.name,
            source=req.source,
            agent_id=req.agent_id,
            prompt_template=req.prompt_template,
            secret=req.secret,
        )
        services.webhook_handler.register(config)
        return {"id": config.id, "status": "registered"}

    @router.get("/v1/webhooks")
    async def list_webhooks():
        return [
            {
                "id": webhook.id,
                "name": webhook.name,
                "source": webhook.source,
                "enabled": webhook.enabled,
            }
            for webhook in services.webhook_handler.list_webhooks()
        ]

    @router.post("/v1/webhooks/{webhook_id}/receive")
    async def receive_webhook(webhook_id: str, body: dict | None = None):
        registered = services.webhook_handler.get(webhook_id)
        payload = body or {}
        event = WebhookEvent(
            source=registered.source if registered else "unknown",
            event_type=payload.get("event", payload.get("action", "unknown")),
            payload=payload,
        )
        result = services.webhook_handler.process(webhook_id, event)
        if not result:
            raise HTTPException(400, "webhook not found or disabled")
        task = await context.scheduler.create_task(
            agent_id=result["agent_id"],
            title=result["title"],
            prompt=result["prompt"],
        )
        return {"task_id": task.id, "status": "created"}

    @router.get("/v1/costs")
    async def get_costs(days: int = 30):
        summary = services.cost_tracker.summary(days=days)
        return {
            "total_cost_usd": round(summary.total_cost_usd, 4),
            "total_requests": summary.total_requests,
            "total_input_tokens": summary.total_input_tokens,
            "total_output_tokens": summary.total_output_tokens,
            "by_agent": {key: round(value, 4) for key, value in summary.by_agent.items()},
            "by_model": {key: round(value, 4) for key, value in summary.by_model.items()},
            "today_usd": round(services.cost_tracker.today_cost(), 4),
            "over_budget": services.cost_tracker.is_over_budget(),
        }

    @router.get("/v1/rate-limits")
    async def get_rate_limits():
        return services.rate_limiter.all_usage()

    @router.get("/v1/profiles")
    async def list_profiles():
        return [
            {
                "id": profile.id,
                "name": profile.name,
                "description": profile.description,
                "permission_mode": profile.permission_mode,
                "skills": profile.skills,
            }
            for profile in services.profile_manager.list_all()
        ]

    @router.get("/v1/profiles/{profile_id}")
    async def get_profile(profile_id: str):
        profile = services.profile_manager.get(profile_id)
        if not profile:
            raise HTTPException(404, "profile not found")
        return {
            "id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "config": services.profile_manager.apply(profile_id),
        }

    @router.get("/v1/workspaces")
    async def list_workspaces():
        return services.workspace_manager.list_active()

    @router.get("/v1/workspaces/usage")
    async def workspace_usage():
        return services.workspace_manager.disk_usage()

    @router.post("/v1/workspaces/cleanup")
    async def cleanup_workspaces(max_age_hours: int = 24):
        return {
            "removed": services.workspace_manager.cleanup_all(
                max_age_hours=max_age_hours
            )
        }

    @router.get("/v1/logs/{task_id}")
    async def get_task_log(task_id: str):
        return [
            {"timestamp": entry.timestamp, "step": entry.step, "data": entry.data}
            for entry in services.task_log.get_log(task_id)
        ]

    @router.get("/v1/logs/{task_id}/timeline")
    async def get_task_timeline(task_id: str):
        return services.task_log.get_timeline(task_id)

    @router.get("/v1/logs/{task_id}/errors")
    async def get_task_errors(task_id: str):
        return [
            {
                "timestamp": entry.timestamp,
                "error": entry.data.get("error", ""),
                "recoverable": entry.data.get("recoverable", False),
            }
            for entry in services.task_log.get_errors(task_id)
        ]

    @router.post("/v1/heartbeat")
    async def agent_heartbeat(req: HeartbeatRequest):
        await services.heartbeat_monitor.report(
            req.agent_id,
            status=req.status,
            active_tasks=req.active_tasks,
        )
        return {"ok": True}

    @router.get("/v1/heartbeat")
    async def agent_statuses():
        return [
            {
                "agent_id": heartbeat.agent_id,
                "status": heartbeat.status,
                "active_tasks": heartbeat.active_tasks,
                "last_seen": heartbeat.last_seen,
            }
            for heartbeat in services.heartbeat_monitor.all_statuses()
        ]

    @router.get("/v1/heartbeat/{agent_id}")
    async def agent_heartbeat_status(agent_id: str):
        heartbeat = services.heartbeat_monitor.get_status(agent_id)
        if not heartbeat:
            return {"agent_id": agent_id, "status": "unknown"}
        return {
            "agent_id": heartbeat.agent_id,
            "status": heartbeat.status,
            "active_tasks": heartbeat.active_tasks,
            "last_seen": heartbeat.last_seen,
        }

    @router.get("/v1/schedules")
    async def list_schedules():
        return [
            {
                "id": schedule.id,
                "name": schedule.name,
                "agent_id": schedule.agent_id,
                "interval_minutes": schedule.interval_minutes,
                "daily_at": schedule.daily_at,
                "enabled": schedule.enabled,
                "last_run": schedule.last_run,
                "run_count": schedule.run_count,
            }
            for schedule in services.recurring_scheduler.list_all()
        ]

    @router.post("/v1/schedules")
    async def create_schedule(req: ScheduleRequest):
        schedule = RecurringSchedule(
            id=req.id,
            name=req.name,
            agent_id=req.agent_id,
            prompt=req.prompt,
            interval_minutes=req.interval_minutes,
            daily_at=req.daily_at,
            provider=req.provider,
            max_runs=req.max_runs,
        )
        services.recurring_scheduler.add(schedule)
        return {"id": schedule.id, "status": "created"}

    @router.delete("/v1/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: str):
        if services.recurring_scheduler.remove(schedule_id):
            return {"status": "deleted"}
        raise HTTPException(404, "schedule not found")

    @router.post("/v1/schedules/{schedule_id}/enable")
    async def enable_schedule(schedule_id: str):
        services.recurring_scheduler.enable(schedule_id)
        return {"status": "enabled"}

    @router.post("/v1/schedules/{schedule_id}/disable")
    async def disable_schedule(schedule_id: str):
        services.recurring_scheduler.disable(schedule_id)
        return {"status": "disabled"}

    return router
