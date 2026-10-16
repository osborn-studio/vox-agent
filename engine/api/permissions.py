"""Principal-scoped permission reconciliation and decision routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..app_context import AppContext
from ..auth import Edition, Principal, require_principal
from ..models import ActivityEntry


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1, max_length=160)
    approved: StrictBool
    remember_scope: StrictBool = False


def create_router(context: AppContext) -> APIRouter:
    """Bind permission routes without inheriting the legacy admin router."""

    router = APIRouter(tags=["permissions"])

    def include_unowned() -> bool:
        return context.auth_service.edition is Edition.PERSONAL

    @router.get("/v1/permissions/pending")
    async def list_pending_permissions(
        principal: Principal = Depends(require_principal),
    ):
        return context.permission_manager.list_pending_requests_for_principal(
            requester_user_id=principal.user_id,
            workspace_id=principal.org_id,
            include_unowned=include_unowned(),
        )

    @router.post("/v1/permissions/approve")
    async def approve_permission(
        req: ApproveRequest,
        principal: Principal = Depends(require_principal),
    ):
        if not context.permission_manager.resolve_approval_for_principal(
            req.approval_id,
            req.approved,
            requester_user_id=principal.user_id,
            workspace_id=principal.org_id,
            remember_scope=req.remember_scope,
            include_unowned=include_unowned(),
        ):
            # Keep wrong-principal, duplicate, unknown, and expired responses
            # indistinguishable so request identities cannot be enumerated.
            raise HTTPException(404, "approval request not found or expired")
        decision = "approved" if req.approved else "denied"
        context.store.insert_activity(ActivityEntry(
            actor_type="user",
            actor_id=principal.user_id,
            action=f"permission:{decision}",
            target_type="permission",
            target_id=req.approval_id,
            details={
                "decision": decision,
                "remember_scope": req.remember_scope,
                "workspace_id": principal.org_id,
                "auth_method": principal.auth_method,
            },
        ))
        return {"ok": True, "decision": decision}

    return router
