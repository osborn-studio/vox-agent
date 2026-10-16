"""Task analytics, organization, search, export, and diagnostics routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..agent_groups import AgentGroup
from ..app_context import AppContext
from ..batch import BatchItem
from ..metrics import metrics as engine_metrics
from ..models import TaskPriority
from ..search import SearchQuery


class UseTemplateRequest(BaseModel):
    template_id: str
    input_text: str
    agent_id: str | None = None
    provider: str = "claude"


class BatchCreateRequest(BaseModel):
    items: list[dict]
    provider: str = "claude"


class BatchStatusRequest(BaseModel):
    task_ids: list[str]


class SearchRequest(BaseModel):
    text: str = ""
    agent_id: str | None = None
    status: str | None = None
    priority: int | None = None
    from_date: str | None = None
    to_date: str | None = None
    limit: int = 50
    offset: int = 0
    sort_by: str = "date"


class SetAnnotationRequest(BaseModel):
    key: str
    value: str


class CreateGroupRequest(BaseModel):
    id: str
    name: str
    emoji: str = "📁"
    members: list[str] = Field(default_factory=list)
    strategy: str = "round_robin"


class AddCommentRequest(BaseModel):
    content: str
    author_type: str = "user"
    author_id: str = "gilbert"


def create_router(context: AppContext) -> APIRouter:
    """Bind task-oriented routes to one isolated application context."""

    router = APIRouter(tags=["tasks"])
    services = context.api

    @router.get("/v1/analytics")
    async def get_analytics():
        stats = services.task_analytics.engine_stats()
        return {
            "total_tasks": stats.total_tasks,
            "completed": stats.completed,
            "failed": stats.failed,
            "cancelled": stats.cancelled,
            "active": stats.active,
            "tasks_today": stats.tasks_today,
            "tasks_this_week": stats.tasks_this_week,
            "agents": [
                {
                    "agent_id": agent.agent_id,
                    "total_tasks": agent.total_tasks,
                    "completed": agent.completed,
                    "failed": agent.failed,
                    "success_rate": round(agent.success_rate, 2),
                    "avg_duration_sec": round(agent.avg_duration_sec, 1),
                }
                for agent in stats.agents
            ],
        }

    @router.get("/v1/analytics/{agent_id}")
    async def get_agent_analytics(agent_id: str):
        stats = services.task_analytics.agent_stats(agent_id)
        return {
            "agent_id": stats.agent_id,
            "total_tasks": stats.total_tasks,
            "completed": stats.completed,
            "failed": stats.failed,
            "cancelled": stats.cancelled,
            "active": stats.active,
            "success_rate": round(stats.success_rate, 2),
            "avg_duration_sec": round(stats.avg_duration_sec, 1),
        }

    @router.get("/v1/templates")
    async def list_templates():
        return [
            {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "agent_id": template.agent_id,
                "priority": template.priority,
                "tags": template.tags,
            }
            for template in services.template_manager.list_all()
        ]

    @router.get("/v1/templates/{template_id}")
    async def get_template(template_id: str):
        template = services.template_manager.get(template_id)
        if not template:
            raise HTTPException(404, "template not found")
        return {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "agent_id": template.agent_id,
            "prompt_template": template.prompt_template,
            "priority": template.priority,
            "skills": template.skills,
            "tags": template.tags,
        }

    @router.post("/v1/templates/use")
    async def use_template(req: UseTemplateRequest):
        rendered = services.template_manager.render(req.template_id, req.input_text)
        if not rendered:
            raise HTTPException(404, "template not found")
        task = await context.scheduler.create_task(
            agent_id=req.agent_id or rendered["agent_id"],
            title=rendered["title"],
            prompt=rendered["prompt"],
            priority=TaskPriority(rendered["priority"]),
            provider=req.provider,
        )
        return {
            "task_id": task.id,
            "status": task.status.value,
            "template": req.template_id,
        }

    @router.post("/v1/batch/create")
    async def batch_create(req: BatchCreateRequest):
        items = [
            BatchItem(
                title=item["title"],
                prompt=item.get("prompt", item["title"]),
                agent_id=item.get("agent_id", "claude-code"),
                priority=item.get("priority", 1),
                depends_on=item.get("depends_on"),
            )
            for item in req.items
        ]
        result = await services.batch_operations.create_batch(items, provider=req.provider)
        return {
            "total": result.total,
            "created": result.created,
            "task_ids": result.task_ids,
            "errors": result.errors,
        }

    @router.post("/v1/batch/cancel/{agent_id}")
    async def batch_cancel(agent_id: str):
        count = await services.batch_operations.cancel_agent_tasks(agent_id)
        return {"cancelled": count, "agent_id": agent_id}

    @router.post("/v1/batch/status")
    async def batch_status(req: BatchStatusRequest):
        return await services.batch_operations.batch_status(req.task_ids)

    @router.post("/v1/search/tasks")
    async def search_tasks(req: SearchRequest):
        result = services.task_search.search(SearchQuery(
            text=req.text,
            agent_id=req.agent_id,
            status=req.status,
            priority=req.priority,
            from_date=req.from_date,
            to_date=req.to_date,
            limit=req.limit,
            offset=req.offset,
            sort_by=req.sort_by,
        ))
        return {"tasks": result.tasks, "total": result.total}

    @router.get("/v1/search/messages")
    async def search_messages(
        text: str,
        task_id: str | None = None,
        limit: int = 50,
    ):
        return services.task_search.search_messages(
            text, task_id=task_id, limit=limit
        )

    @router.get("/v1/search/recent")
    async def recent_activity(hours: int = 24, limit: int = 20):
        return services.task_search.recent_activity(hours=hours, limit=limit)

    @router.post("/v1/export/obsidian")
    async def export_to_obsidian(days: int = 7):
        count = services.task_exporter.export_recent(days=days)
        return {"files_written": count, "days": days}

    @router.get("/v1/export/daily")
    async def export_daily(date: str | None = None):
        return {"report": services.report_exporter.daily_report(date)}

    @router.get("/v1/export/weekly")
    async def export_weekly():
        return {"report": services.report_exporter.weekly_report()}

    @router.get("/v1/export/csv")
    async def export_csv(agent_id: str | None = None):
        return PlainTextResponse(
            services.report_exporter.tasks_csv(agent_id),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=tasks.csv"},
        )

    @router.get("/v1/export/json")
    async def export_json(agent_id: str | None = None):
        return json.loads(services.report_exporter.tasks_json(agent_id))

    @router.get("/v1/health/detailed")
    async def health_detailed():
        report = services.health_checker.check()
        return {
            "status": report.status,
            "uptime_seconds": round(report.uptime_seconds, 1),
            "databases": report.databases,
            "tasks": report.tasks,
            "tools": report.tools,
            "skills": report.skills,
            "memory_agents": report.memory_agents,
            "recent_error_rate": round(report.recent_error_rate, 3),
            "warnings": report.warnings,
        }

    @router.get("/v1/validate")
    async def validate_config():
        result = services.config_validator.validate()
        return {
            "valid": result.valid,
            "checks": result.checks,
            "errors": result.errors,
            "warnings": result.warnings,
        }

    @router.get("/v1/dashboard")
    async def get_dashboard():
        return services.dashboard.get_dashboard()

    @router.get("/v1/metrics")
    async def get_metrics():
        return engine_metrics.snapshot()

    @router.get("/v1/diff/{task_a}/{task_b}")
    async def diff_tasks(task_a: str, task_b: str):
        result = services.task_diff.compare_tasks(task_a, task_b)
        if not result:
            raise HTTPException(404, "one or both tasks not found or have no output")
        return {
            "task_a": result.task_a_id,
            "task_b": result.task_b_id,
            "added": result.added_lines,
            "removed": result.removed_lines,
            "unchanged": result.unchanged_lines,
            "similarity": result.similarity,
            "diff": result.diff_text,
        }

    @router.get("/v1/plugins")
    async def list_plugins():
        return [
            {
                "name": plugin.manifest.name,
                "version": plugin.manifest.version,
                "tools": plugin.tools_loaded,
                "skills": plugin.skills_loaded,
                "hooks": plugin.hooks_loaded,
                "errors": plugin.errors,
            }
            for plugin in services.plugin_loader.get_loaded()
        ]

    @router.get("/v1/conversations")
    async def list_conversations():
        return [
            {
                "agent_id": agent_id,
                "messages": services.conversation_manager.message_count(agent_id),
            }
            for agent_id in services.conversation_manager.active_conversations()
        ]

    @router.get("/v1/tasks/{task_id}/history")
    async def get_task_history(task_id: str):
        return services.task_history.get_timeline(task_id)

    @router.get("/v1/tasks/{task_id}/duration")
    async def get_task_duration(task_id: str):
        return services.task_history.time_in_status(task_id)

    @router.get("/v1/queue")
    async def get_queue_stats():
        return services.queue_stats.summary()

    @router.get("/v1/queue/depth")
    async def get_queue_depth():
        return services.queue_stats.queue_depth()

    @router.get("/v1/queue/throughput")
    async def get_throughput(minutes: int = 60):
        return services.queue_stats.throughput(minutes)

    @router.post("/v1/replay/{task_id}")
    async def replay_task(task_id: str, speed: float = 1.0):
        count = await services.event_replay.replay(task_id, speed=speed)
        return {"replayed": count, "speed": speed}

    @router.get("/v1/replay/{task_id}/summary")
    async def replay_summary(task_id: str):
        result = await services.event_replay.replay_summary(task_id)
        if not result:
            raise HTTPException(404, "task not found")
        return result

    @router.post("/v1/tasks/{task_id}/annotations")
    async def set_annotation(task_id: str, req: SetAnnotationRequest):
        services.annotation_manager.set(task_id, req.key, req.value)
        return {"ok": True}

    @router.get("/v1/tasks/{task_id}/annotations")
    async def get_annotations(task_id: str):
        return services.annotation_manager.get_all(task_id)

    @router.delete("/v1/tasks/{task_id}/annotations/{key}")
    async def delete_annotation(task_id: str, key: str):
        if services.annotation_manager.delete(task_id, key):
            return {"ok": True}
        raise HTTPException(404, "annotation not found")

    @router.get("/v1/groups")
    async def list_groups():
        return [
            {
                "id": group.id,
                "name": group.name,
                "emoji": group.emoji,
                "members": group.members,
                "strategy": group.strategy,
            }
            for group in services.group_manager.list_all()
        ]

    @router.post("/v1/groups")
    async def create_group(req: CreateGroupRequest):
        services.group_manager.create(AgentGroup(
            id=req.id,
            name=req.name,
            emoji=req.emoji,
            members=req.members,
            strategy=req.strategy,
        ))
        return {"id": req.id, "status": "created"}

    @router.delete("/v1/groups/{group_id}")
    async def delete_group(group_id: str):
        if services.group_manager.delete(group_id):
            return {"status": "deleted"}
        raise HTTPException(404, "group not found")

    @router.post("/v1/groups/{group_id}/members/{agent_id}")
    async def add_group_member(group_id: str, agent_id: str):
        if services.group_manager.add_member(group_id, agent_id):
            return {"status": "added"}
        raise HTTPException(404, "group not found")

    @router.delete("/v1/groups/{group_id}/members/{agent_id}")
    async def remove_group_member(group_id: str, agent_id: str):
        if services.group_manager.remove_member(group_id, agent_id):
            return {"status": "removed"}
        raise HTTPException(404, "group or member not found")

    @router.get("/v1/capabilities")
    async def list_capabilities():
        return [
            services.capability_manager.summary(capability.agent_id)
            for capability in services.capability_manager.list_all()
        ]

    @router.get("/v1/capabilities/{agent_id}")
    async def get_capabilities(agent_id: str):
        return services.capability_manager.summary(agent_id)

    @router.post("/v1/session/start")
    async def start_session():
        session = services.session_manager.start()
        return {"session_id": session.id, "started_at": session.started_at}

    @router.post("/v1/session/end")
    async def end_session():
        session = services.session_manager.end()
        if not session:
            return {"message": "no active session"}
        return {
            "session_id": session.id,
            "duration_seconds": session.duration_seconds,
            "task_count": session.task_count,
            "message_count": session.message_count,
            "agents_used": session.agents_used,
        }

    @router.get("/v1/session")
    async def get_session():
        session = services.session_manager.current
        if not session:
            return {"active": False}
        return {
            "active": True,
            "session_id": session.id,
            "started_at": session.started_at,
        }

    @router.post("/v1/tasks/{task_id}/favorite")
    async def toggle_favorite(task_id: str):
        return {"favorite": services.favorites_manager.toggle(task_id)}

    @router.get("/v1/favorites")
    async def list_favorites():
        return services.favorites_manager.list_favorites()

    @router.post("/v1/tasks/{task_id}/tags/{tag}")
    async def add_tag(task_id: str, tag: str):
        services.tag_manager.add_tag(task_id, tag)
        return {"ok": True, "tags": services.tag_manager.get_tags(task_id)}

    @router.delete("/v1/tasks/{task_id}/tags/{tag}")
    async def remove_tag(task_id: str, tag: str):
        services.tag_manager.remove_tag(task_id, tag)
        return {"ok": True, "tags": services.tag_manager.get_tags(task_id)}

    @router.get("/v1/tasks/{task_id}/tags")
    async def get_tags(task_id: str):
        return services.tag_manager.get_tags(task_id)

    @router.get("/v1/tags")
    async def all_tags():
        return services.tag_manager.all_tags()

    @router.get("/v1/tags/{tag}/tasks")
    async def tasks_by_tag(tag: str):
        return services.tag_manager.find_by_tag(tag)

    @router.post("/v1/tasks/{task_id}/comments")
    async def add_comment(task_id: str, req: AddCommentRequest):
        comment = services.comment_manager.add(
            task_id, req.content, req.author_type, req.author_id
        )
        return {
            "id": comment.id,
            "content": comment.content,
            "created_at": comment.created_at,
        }

    @router.get("/v1/tasks/{task_id}/comments")
    async def list_comments(task_id: str):
        return [
            {
                "id": comment.id,
                "author_type": comment.author_type,
                "author_id": comment.author_id,
                "content": comment.content,
                "created_at": comment.created_at,
            }
            for comment in services.comment_manager.list_for_task(task_id)
        ]

    @router.delete("/v1/comments/{comment_id}")
    async def delete_comment(comment_id: str):
        if services.comment_manager.delete(comment_id):
            return {"ok": True}
        raise HTTPException(404, "comment not found")

    return router
