"""Principal-bound organization collaboration routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..app_context import AppContext
from ..auth import Principal, require_principal
from ..org_api import (
    _audit_ids_for_org,
    _project_report,
    _team_summary,
    _visible_events_for_day,
)
from ..org_models import RequestContext
from ..source_adapters import manual_note_to_work_event
from ..status_qa import answer_status_question
from ..work_events import WorkEventSourceType


class PrincipalBoundPayload(BaseModel):
    """Compatibility identity fields are accepted but never used for authorization."""

    model_config = ConfigDict(populate_by_name=True)

    org_id: str | None = Field(default=None, alias="orgId", exclude=True)
    requester_user_id: str | None = Field(
        default=None,
        alias="requesterUserId",
        exclude=True,
    )

    @property
    def supplied_identity_override(self) -> bool:
        return self.org_id is not None or self.requester_user_id is not None


class CreateWorkEventPayload(PrincipalBoundPayload):
    actor_user_id: str | None = Field(default=None, alias="actorUserId", exclude=True)
    body: str = Field(min_length=1, max_length=100_000)
    source_type: WorkEventSourceType = Field(alias="sourceType")
    team_id: str | None = Field(default=None, alias="teamId")
    project_id: str | None = Field(default=None, alias="projectId")
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    private: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def supplied_identity_override(self) -> bool:
        return super().supplied_identity_override or self.actor_user_id is not None


class QueryPayload(PrincipalBoundPayload):
    question: str = Field(min_length=1, max_length=10_000)
    team_id: str | None = Field(default=None, alias="teamId")
    project_id: str | None = Field(default=None, alias="projectId")
    purpose: str = Field(default="status", max_length=120)
    limit: int = Field(default=5, ge=1, le=20)


class DashboardPayload(PrincipalBoundPayload):
    day: str = Field(min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    team_id: str | None = Field(default=None, alias="teamId")
    project_id: str | None = Field(default=None, alias="projectId")


def create_router(context: AppContext) -> APIRouter:
    """Bind org workflows to one app-owned service graph and verified identity."""

    router = APIRouter(prefix="/v1/org", tags=["organization"])

    @router.post("/work-events", status_code=status.HTTP_201_CREATED)
    async def create_work_event(
        payload: CreateWorkEventPayload,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        _require_team_membership(context, principal, payload.team_id)
        _require_project_membership(context, principal, payload.project_id)
        event = manual_note_to_work_event(
            org_id=principal.org_id,
            actor_user_id=principal.user_id,
            note=payload.body,
            team_id=payload.team_id,
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            private=payload.private,
        )
        event.source_type = payload.source_type
        event.metadata = payload.metadata

        request_context = _request_context(
            principal,
            team_id=payload.team_id,
            project_id=payload.project_id,
            purpose="create_work_event",
        )
        decision = context.org_policy.can_read_event(request_context, event)
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "POLICY_DENIED", "reason": decision.reason},
            )

        context.org_event_store.insert_work_event(event)
        context.voice_audit_log.record(
            "org.work_event.created",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            target_id=event.id,
            metadata={
                "sourceType": event.source_type.value,
                "identityOverrideIgnored": payload.supplied_identity_override,
            },
        )
        return {
            "id": event.id,
            "sourceId": event.id,
            "orgId": event.org_id,
            "actorUserId": event.actor_id,
            "teamId": event.team_id,
            "projectId": event.project_id,
            "visibility": event.visibility.value,
            "sourceType": event.source_type.value,
            "createdAt": event.created_at,
        }

    @router.post("/query")
    async def query_status(
        payload: QueryPayload,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        request_context = _request_context(
            principal,
            team_id=payload.team_id,
            project_id=payload.project_id,
            purpose=payload.purpose,
        )
        before_audit_ids = _audit_ids_for_org(
            context.org_access_audit_store,
            principal.org_id,
        )
        answer = answer_status_question(
            context.org_retriever,
            request_context,
            payload.question,
            limit=payload.limit,
        )
        after_audit_ids = _audit_ids_for_org(
            context.org_access_audit_store,
            principal.org_id,
        )
        new_audit_ids = sorted(after_audit_ids - before_audit_ids)
        context.voice_audit_log.record(
            "org.query",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            metadata={
                "sourceCount": len(answer.source_ids),
                "identityOverrideIgnored": payload.supplied_identity_override,
            },
        )
        return {
            "answer": answer.answer,
            "sourceIds": answer.source_ids,
            "citations": [
                {"sourceId": citation["source_id"]}
                for citation in answer.citations
            ],
            "auditIds": new_audit_ids,
            "confidence": answer.confidence,
            "confident": answer.confident,
        }

    @router.post("/dashboard")
    async def dashboard(
        payload: DashboardPayload,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        request_context = _request_context(
            principal,
            team_id=payload.team_id,
            project_id=payload.project_id,
            purpose="dashboard",
        )
        events = _visible_events_for_day(
            event_store=context.org_event_store,
            policy=context.org_policy,
            ctx=request_context,
            day=payload.day,
        )
        team_events = [
            event for event in events if event.get("team_id") == payload.team_id
        ]
        project_events = [
            event for event in events if event.get("project_id") == payload.project_id
        ]
        context.voice_audit_log.record(
            "org.dashboard.viewed",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            metadata={
                "day": payload.day,
                "sourceCount": len(events),
                "identityOverrideIgnored": payload.supplied_identity_override,
            },
        )
        return {
            "teamSummary": _camel_summary(_team_summary(team_events)),
            "projectReport": _camel_summary(_project_report(project_events)),
        }

    return router


def _request_context(
    principal: Principal,
    *,
    team_id: str | None,
    project_id: str | None,
    purpose: str,
) -> RequestContext:
    return RequestContext(
        org_id=principal.org_id,
        requester_user_id=principal.user_id,
        team_id=team_id,
        project_id=project_id,
        purpose=purpose,
    )


def _require_team_membership(
    context: AppContext,
    principal: Principal,
    team_id: str | None,
) -> None:
    if team_id is None:
        return
    team = context.org_store.get_team(team_id)
    if team is None or team["org_id"] != principal.org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TEAM_NOT_FOUND", "message": "Team not found"},
        )
    if context.org_store.get_team_role(principal.user_id, team_id) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "TEAM_MEMBERSHIP_REQUIRED",
                "message": "Team membership required",
            },
        )


def _require_project_membership(
    context: AppContext,
    principal: Principal,
    project_id: str | None,
) -> None:
    if project_id is None:
        return
    project = context.org_store.get_project(project_id)
    if project is None or project["org_id"] != principal.org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROJECT_NOT_FOUND", "message": "Project not found"},
        )
    if context.org_store.get_project_role(principal.user_id, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PROJECT_MEMBERSHIP_REQUIRED",
                "message": "Project membership required",
            },
        )
def _camel_summary(summary: dict[str, Any]) -> dict[str, Any]:
    result = {
        "body": summary["body"],
        "sourceIds": summary["source_ids"],
        "visibility": summary["visibility"],
        "sourceCount": summary["source_count"],
        "confidence": summary["confidence"],
    }
    for key in ("completed", "blocked", "next"):
        if key in summary:
            result[key] = summary[key]
    return result
