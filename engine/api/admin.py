"""Administrative policy and audit routes."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..app_context import AppContext
from ..auth import Principal, require_admin
from ..model_policy import available_answerers, available_asr_adapters
from ..org_models import OrgRole

router = APIRouter(prefix="/v1/admin", tags=["admin"])

_RETENTION_DAY_FIELDS = {
    "audioRetentionDays": "audio_retention_days",
    "transcriptRetentionDays": "transcript_retention_days",
    "memoryRetentionDays": "memory_retention_days",
}


class CreateUserPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(alias="displayName", min_length=1, max_length=120)
    role: OrgRole = OrgRole.MEMBER


class UpdateUserPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str | None = Field(default=None, alias="displayName", min_length=1, max_length=120)
    role: OrgRole | None = None
    status: str | None = Field(default=None, pattern="^(active|suspended)$")


class CreateCredentialPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class RetentionSweepPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dry_run: bool = Field(alias="dryRun")


def _context(request: Request) -> AppContext:
    context = getattr(request.app.state, "context", None)
    if not isinstance(context, AppContext):
        raise RuntimeError("Vox Agents AppContext is not configured")
    return context


@router.get("/audit-events")
async def list_audit_events(
    request: Request,
    limit: int = 50,
    _principal: Principal = Depends(require_admin),
):
    return _context(request).voice_audit_log.list_events(limit=limit)


@router.get("/model-policy")
async def get_model_policy(
    request: Request,
    _principal: Principal = Depends(require_admin),
):
    return _context(request).model_policy.effective()


@router.patch("/model-policy")
async def patch_model_policy(
    request: Request,
    payload: dict,
    principal: Principal = Depends(require_admin),
):
    """Set or clear runtime model-routing overrides."""
    policy = _context(request).model_policy
    if "answerer" in payload:
        value = payload["answerer"]
        if value is not None and value not in available_answerers():
            raise HTTPException(400, f"unknown answerer: {value!r}")
        policy.answerer = value
    if "asrAdapter" in payload:
        value = payload["asrAdapter"]
        if value is not None and value not in available_asr_adapters():
            raise HTTPException(400, f"unknown asrAdapter: {value!r}")
        policy.asr_adapter = value
    changes = {key: payload[key] for key in ("answerer", "asrAdapter") if key in payload}
    if changes:
        context = _context(request)
        context.policy_store.save_model_policy(policy)
        context.voice_audit_log.record(
            "admin.model-policy.updated",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            metadata={"changes": changes},
        )
    return policy.effective()


@router.get("/retention-policy")
async def get_retention_policy(
    request: Request,
    _principal: Principal = Depends(require_admin),
):
    return _context(request).retention_policy.effective()


@router.patch("/retention-policy")
async def patch_retention_policy(
    request: Request,
    payload: dict,
    principal: Principal = Depends(require_admin),
):
    """Set or clear validated runtime data-retention overrides."""
    policy = _context(request).retention_policy
    for key, attr in _RETENTION_DAY_FIELDS.items():
        if key not in payload:
            continue
        value = payload[key]
        if value is None:
            setattr(policy, attr, None)
            continue
        # bool is a subclass of int; accepting True would silently mean one day.
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise HTTPException(400, f"{key} must be a positive integer or null, got {value!r}")
        setattr(policy, attr, value)
    if "autoDeleteEnabled" in payload:
        value = payload["autoDeleteEnabled"]
        if value is not None and not isinstance(value, bool):
            raise HTTPException(400, f"autoDeleteEnabled must be a boolean or null, got {value!r}")
        policy.auto_delete_enabled = value
    changes = {
        key: payload[key]
        for key in (*_RETENTION_DAY_FIELDS, "autoDeleteEnabled")
        if key in payload
    }
    if changes:
        context = _context(request)
        context.policy_store.save_retention_policy(policy)
        context.voice_audit_log.record(
            "admin.retention-policy.updated",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            metadata={"changes": changes},
        )
    return policy.effective()


@router.get("/retention/sweeps/latest")
async def latest_retention_sweep(
    request: Request,
    _principal: Principal = Depends(require_admin),
):
    return _context(request).policy_store.latest_retention_sweep()


@router.post("/retention/sweeps")
async def run_retention_sweep(
    request: Request,
    payload: RetentionSweepPayload,
    principal: Principal = Depends(require_admin),
):
    return await run_in_threadpool(
        _context(request).retention_sweeper.run,
        dry_run=payload.dry_run,
        actor_id=principal.user_id,
    )


@router.get("/users")
async def list_users(
    request: Request,
    principal: Principal = Depends(require_admin),
):
    return [
        _user_response(row)
        for row in _context(request).org_store.list_users(principal.org_id)
    ]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request,
    payload: CreateUserPayload,
    principal: Principal = Depends(require_admin),
):
    context = _context(request)
    try:
        user = context.auth_service.create_user(
            org_id=principal.org_id,
            email=payload.email,
            display_name=payload.display_name,
            role=payload.role.value,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "a user with this email already exists") from exc
    context.voice_audit_log.record(
        "admin.user.created",
        actor_id=principal.user_id,
        workspace_id=principal.org_id,
        target_id=user["id"],
        metadata={"role": user["role"]},
    )
    return _user_response(user)


@router.patch("/users/{user_id}")
async def update_user(
    request: Request,
    user_id: str,
    payload: UpdateUserPayload,
    principal: Principal = Depends(require_admin),
):
    context = _context(request)
    existing = context.org_store.get_user(user_id)
    if existing is None or existing["org_id"] != principal.org_id:
        raise HTTPException(404, "user not found")
    changes = {
        key: value
        for key, value in {
            "display_name": payload.display_name,
            "role": payload.role.value if payload.role is not None else None,
            "status": payload.status,
        }.items()
        if value is not None
    }
    if not changes:
        raise HTTPException(400, "at least one user field is required")
    updated = context.org_store.update_user(user_id, **changes)
    context.voice_audit_log.record(
        "admin.user.updated",
        actor_id=principal.user_id,
        workspace_id=principal.org_id,
        target_id=user_id,
        metadata={"changes": changes},
    )
    return _user_response(updated)


@router.post("/users/{user_id}/credentials", status_code=status.HTTP_201_CREATED)
async def create_user_credential(
    request: Request,
    user_id: str,
    payload: CreateCredentialPayload,
    principal: Principal = Depends(require_admin),
):
    context = _context(request)
    user = context.org_store.get_user(user_id)
    if user is None or user["org_id"] != principal.org_id:
        raise HTTPException(404, "user not found")
    try:
        credential, token = context.auth_service.issue_credential(user_id, payload.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    context.voice_audit_log.record(
        "admin.credential.created",
        actor_id=principal.user_id,
        workspace_id=principal.org_id,
        target_id=credential["id"],
        metadata={"userId": user_id, "name": credential["name"]},
    )
    return {**credential, "token": token}


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_credential(
    request: Request,
    credential_id: str,
    principal: Principal = Depends(require_admin),
):
    context = _context(request)
    if not context.auth_service.revoke_credential(credential_id, org_id=principal.org_id):
        raise HTTPException(404, "credential not found")
    context.voice_audit_log.record(
        "admin.credential.revoked",
        actor_id=principal.user_id,
        workspace_id=principal.org_id,
        target_id=credential_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _user_response(user: dict) -> dict:
    return {
        "id": user["id"],
        "orgId": user["org_id"],
        "email": user["email"],
        "displayName": user["display_name"],
        "role": user["role"],
        "status": user["status"],
        "createdAt": user["created_at"],
    }
