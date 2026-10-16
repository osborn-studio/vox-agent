"""FastAPI surface for organization collaboration workflows."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from .access_audit import AccessAuditStore
from .org_models import RequestContext
from .org_store import OrgStore
from .permissioned_retrieval import PermissionedRetriever
from .policy import PolicyEngine
from .source_adapters import manual_note_to_work_event
from .status_qa import answer_status_question
from .work_events import WorkEventSourceType, WorkEventStore, WorkEventVisibility


class RequestContextPayload(BaseModel):
    org_id: str = Field(min_length=1)
    requester_user_id: str = Field(min_length=1)
    team_id: str | None = None
    project_id: str | None = None
    purpose: str = ""

    def to_context(self) -> RequestContext:
        return RequestContext(
            org_id=self.org_id,
            requester_user_id=self.requester_user_id,
            team_id=self.team_id,
            project_id=self.project_id,
            purpose=self.purpose,
        )


class CreateWorkEventPayload(BaseModel):
    org_id: str = Field(min_length=1)
    actor_user_id: str = Field(min_length=1)
    requester_user_id: str = Field(min_length=1)
    body: str = Field(min_length=1)
    source_type: WorkEventSourceType
    team_id: str | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    private: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryPayload(BaseModel):
    context: RequestContextPayload
    question: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class DashboardPayload(BaseModel):
    context: RequestContextPayload
    day: str = Field(min_length=10, max_length=10)


def create_org_collab_app(data_dir: Path | str | None = None) -> FastAPI:
    """Create an isolated org-collab API app backed by stores under data_dir."""

    resolved_data_dir = Path(data_dir) if data_dir is not None else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        org_store = OrgStore(resolved_data_dir)
        event_store = WorkEventStore(resolved_data_dir)
        audit_store = AccessAuditStore(resolved_data_dir)
        org_store.connect()
        event_store.connect()
        audit_store.connect()

        policy = PolicyEngine(org_store=org_store, audit_store=audit_store)
        app.state.org_store = org_store
        app.state.event_store = event_store
        app.state.audit_store = audit_store
        app.state.policy = policy
        app.state.retriever = PermissionedRetriever(
            event_store=event_store,
            policy=policy,
        )

        try:
            yield
        finally:
            audit_store.close()
            event_store.close()
            org_store.close()

    app = FastAPI(lifespan=lifespan)

    @app.post("/org/work-events", status_code=status.HTTP_201_CREATED)
    async def create_work_event(payload: CreateWorkEventPayload) -> dict[str, Any]:
        event = manual_note_to_work_event(
            org_id=payload.org_id,
            actor_user_id=payload.actor_user_id,
            note=payload.body,
            team_id=payload.team_id,
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            private=payload.private,
        )
        event.source_type = payload.source_type
        event.metadata = payload.metadata

        ctx = RequestContext(
            org_id=payload.org_id,
            requester_user_id=payload.requester_user_id,
            team_id=payload.team_id,
            project_id=payload.project_id,
            purpose="create_work_event",
        )
        decision = app.state.policy.can_read_event(ctx, event)
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "POLICY_DENIED", "reason": decision.reason},
            )

        app.state.event_store.insert_work_event(event)
        return {
            "id": event.id,
            "source_id": event.id,
            "org_id": event.org_id,
            "team_id": event.team_id,
            "project_id": event.project_id,
            "visibility": event.visibility.value,
            "source_type": event.source_type.value,
            "created_at": event.created_at,
        }

    @app.post("/org/query")
    async def query_status(payload: QueryPayload) -> dict[str, Any]:
        before_audit_ids = _audit_ids_for_org(
            app.state.audit_store,
            payload.context.org_id,
        )
        answer = answer_status_question(
            app.state.retriever,
            payload.context.to_context(),
            payload.question,
            limit=payload.limit,
        )
        after_audit_ids = _audit_ids_for_org(
            app.state.audit_store,
            payload.context.org_id,
        )
        return {
            "answer": answer.answer,
            "source_ids": answer.source_ids,
            "citations": answer.citations,
            "audit_ids": [
                audit_id
                for audit_id in after_audit_ids
                if audit_id not in before_audit_ids
            ],
            "confidence": answer.confidence,
            "confident": answer.confident,
        }

    @app.post("/org/dashboard")
    async def dashboard(payload: DashboardPayload) -> dict[str, Any]:
        ctx = payload.context.to_context()
        events = _visible_events_for_day(
            event_store=app.state.event_store,
            policy=app.state.policy,
            ctx=ctx,
            day=payload.day,
        )
        team_events = [event for event in events if event.get("team_id") == ctx.team_id]
        project_events = [
            event for event in events if event.get("project_id") == ctx.project_id
        ]
        return {
            "team_summary": _team_summary(team_events),
            "project_report": _project_report(project_events),
        }

    return app


def _visible_events_for_day(
    *,
    event_store: WorkEventStore,
    policy: PolicyEngine,
    ctx: RequestContext,
    day: str,
) -> list[dict[str, Any]]:
    candidates = event_store.list_work_events(
        org_id=ctx.org_id,
        team_id=ctx.team_id,
        project_id=ctx.project_id,
        limit=1000,
    )
    visible = []
    for event in candidates:
        if not str(event["created_at"]).startswith(day):
            continue
        if policy.can_read_event(ctx, event).allowed:
            visible.append(event)
    return sorted(visible, key=lambda event: event["created_at"])


def _audit_ids_for_org(audit_store: AccessAuditStore, org_id: str) -> set[str]:
    return {
        record["id"]
        for record in audit_store.list_audit_records(org_id, limit=1000)
    }


def _team_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    return _summary(
        events,
        visibility=WorkEventVisibility.TEAM_SUMMARY,
        body=_body_from_events(events),
    )


def _project_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    completed: list[str] = []
    blocked: list[str] = []
    next_items: list[str] = []
    for event in events:
        body = event["body"]
        lowered = body.lower()
        metadata = event.get("metadata", {})
        if str(metadata.get("status", "")).lower() == "completed":
            completed.append(str(metadata.get("summary") or body))
        if metadata.get("blocked"):
            blocked.append(str(metadata["blocked"]))
        if metadata.get("next"):
            next_items.append(str(metadata["next"]))
        if lowered.startswith(("completed ", "complete ", "done ", "shipped ")):
            completed.append(body)
        elif lowered.startswith(("blocked ", "blocker ", "stuck ")):
            blocked.append(body)
        elif lowered.startswith(("next ", "todo ", "follow up ")):
            next_items.append(body)

    report = _summary(
        events,
        visibility=WorkEventVisibility.PROJECT_SUMMARY,
        body=_progress_body(completed, blocked, next_items),
    )
    report["completed"] = completed
    report["blocked"] = blocked
    report["next"] = next_items
    return report


def _summary(
    events: list[dict[str, Any]],
    *,
    visibility: WorkEventVisibility,
    body: str,
) -> dict[str, Any]:
    source_ids = [event["id"] for event in events]
    source_count = len(source_ids)
    return {
        "body": body,
        "source_ids": source_ids,
        "visibility": visibility.value,
        "source_count": source_count,
        "confidence": _confidence(source_count),
    }


def _body_from_events(events: list[dict[str, Any]]) -> str:
    if not events:
        return "No work events found."
    return "\n".join(event["body"] for event in events)


def _progress_body(
    completed: list[str],
    blocked: list[str],
    next_items: list[str],
) -> str:
    sections = [
        ("Completed", completed),
        ("Blocked", blocked),
        ("Next", next_items),
    ]
    return "\n".join(
        f"{name}: {', '.join(items) if items else 'None'}" for name, items in sections
    )


def _confidence(source_count: int) -> float:
    if source_count <= 0:
        return 0.0
    return min(1.0, 0.5 + source_count * 0.1)
