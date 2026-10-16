"""Independent enterprise backup HTTP surface; no agent execution imports."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .context_projects import ContextProject
from .auth import AuthService, Edition, Principal, require_principal
from .input_backup import (
    BackupConflict,
    BackupInput,
    BackupStore,
    BackupTooLarge,
    DeliveryPaused,
    Target,
)


from .feishu_client import CHAT_ID_PATTERN

ChatID = Annotated[str, Field(pattern=CHAT_ID_PATTERN, max_length=256)]
PolicyText = Annotated[str, Field(min_length=1, max_length=256, pattern=r"\S")]


class SharedReadPolicy(BaseModel):
    """Operator-reviewed Vox authorization metadata, not a Feishu visibility claim."""

    model_config = ConfigDict(extra="forbid", strict=True)
    chat_id: ChatID
    created_by: PolicyText
    effective_at: str
    version: int = Field(ge=1)
    audit_ref: PolicyText
    scope: Literal["current_members"]
    revoked_at: str | None = None
    # Explicit operator policy; display sharing alone does not authorize model processing.
    allow_model_translation: bool = False

    @field_validator("created_by", "audit_ref")
    @classmethod
    def unambiguous_audit_identifier(cls, value):
        if not value.isprintable() or value != value.strip():
            raise ValueError("audit identifiers must be printable with no surrounding whitespace")
        return value

    @field_validator("effective_at", "revoked_at")
    @classmethod
    def aware_timestamp(cls, value):
        if value is not None:
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("policy timestamps require a timezone")
        return value

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.revoked_at is not None and (
            datetime.fromisoformat(self.revoked_at) < datetime.fromisoformat(self.effective_at)
        ):
            raise ValueError("revocation precedes policy effectiveness")
        return self


class ConversationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pinned_chat_ids: list[ChatID] = Field(default_factory=list)
    denied_chat_ids: list[ChatID] = Field(default_factory=list)
    shared_read_chat_ids: list[SharedReadPolicy] = Field(default_factory=list)

    @model_validator(mode="after")
    def unambiguous_chats(self):
        shared = [entry.chat_id for entry in self.shared_read_chat_ids]
        for ids in (self.pinned_chat_ids, self.denied_chat_ids, shared):
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate conversation policy chat")
        if set(self.denied_chat_ids) & (set(self.pinned_chat_ids) | set(shared)):
            raise ValueError("denied chats cannot be pinned or shared")
        return self


@dataclass
class BackupAppContext:
    auth_service: AuthService
    backup_store: BackupStore
    targets: tuple[Target, ...]
    enabled: bool = False
    conversations_enabled: bool = False
    conversation_policy: ConversationPolicy = field(default_factory=ConversationPolicy)

    projects: tuple[ContextProject, ...] = ()
    private_context_enabled: bool = False
    private_context_store: object | None = field(default=None, init=False)

    conversation_discovery: object | None = None
    conversation_store: object | None = None
    conversation_reading: object | None = None

    def __post_init__(self):
        if self.auth_service.edition is not Edition.ENTERPRISE:
            raise ValueError("backup service requires enterprise authentication")
        if len({(t.org_id, t.id) for t in self.targets}) != len(self.targets):
            raise ValueError("duplicate target configuration")
        if len({(p.org_id, p.id) for p in self.projects}) != len(self.projects):
            raise ValueError("duplicate project configuration")

        if self.private_context_enabled:
            from .context_private import PrivateContextJournal

            self.private_context_store = PrivateContextJournal(self.backup_store.path)

    def authorize_delivery(self, job: dict) -> bool:
        if not self.enabled:
            raise DeliveryPaused()
        user = self.auth_service.org_store.get_user(job["actor"])
        if not user or user["status"] != "active" or user["org_id"] != job["org"]:
            return False
        try:
            project_id, repository_id = self.backup_store.project_attribution(job["id"])
        except (LookupError, ValueError, TypeError, RecursionError):
            return False
        if project_id is not None and not any(
            p.id == project_id and p.org_id == job["org"]
            and job["actor"] in p.allowed_users and p.repository_id == repository_id
            for p in self.projects
        ):
            return False
        return any(
            t.org_id == job["org"]
            and t.id == job["target"]
            and t.version == job["version"]
            and t.chat_id == job["chat"]
            and self.allowed_user(t, job["actor"])
            for t in self.targets
        )

    def allowed_user(self, target, user_id):
        if user_id in target.allowed_users:
            return True
        login = getattr(self.auth_service, "feishu_login", None)
        return bool(
            login
            and target.id in login.settings.backup_target_ids
            and login.permits(user_id, target.org_id)
        )


def require_feishu_principal(
    request: Request, principal: Principal = Depends(require_principal)
) -> Principal:
    """Conversation-only gate; never infer Feishu identity from headers/token shape."""
    context = request.app.state.context
    if not context.conversations_enabled:
        raise HTTPException(503, "CONVERSATIONS_DISABLED")
    login = getattr(context.auth_service, "feishu_login", None)
    current = login.resolve_conversation_principal(principal) if login is not None else None
    if current is None:
        context.auth_service.audit_log.record(
            "feishu.conversation.authorization.denied",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            metadata={"reason": "FEISHU_PRINCIPAL_REQUIRED"},
        )
        raise HTTPException(403, "FEISHU_PRINCIPAL_REQUIRED")
    return current


def create_backup_app(
    context: BackupAppContext | None = None, *, lifespan=None, enable_feishu_login=False,
    enable_conversations=False, enable_private_context=False
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.context = context
    from .context_inputs import register_context_input_routes

    register_context_input_routes(app)
    if enable_private_context or (context is not None and context.private_context_enabled):
        from .context_private import register_private_context_routes

        register_private_context_routes(app)
    from .feishu_login import login_router

    from .feishu_conversations import conversation_router

    if enable_conversations or (context is not None and context.conversations_enabled):
        app.include_router(conversation_router())
    if enable_feishu_login:
        app.include_router(login_router())

    @app.middleware("http")
    async def protect_login_responses(request, call_next):
        login = getattr(request.app.state, "feishu_login", None)
        if login:
            from urllib.parse import urlsplit

            expected = urlsplit(login.settings.public_base_url)
            if request.headers.get("host", "") != expected.netloc:
                return JSONResponse(status_code=400, content={"detail": "INVALID_HOST"},
                                    headers={"Cache-Control": "no-store"})
            if (
                request.url.path.startswith("/v1/auth/")
                and request.method == "POST"
                and request.headers.get("origin")
            ):
                return JSONResponse(status_code=403, content={"detail": "NATIVE_CLIENT_REQUIRED"})
        response = await call_next(request)
        if request.url.path.startswith(("/v1/conversations", "/v1/context-inputs", "/v1/context-weekly-draft", "/v1/context-private")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/v1/auth/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'"
            )
        return response

    def current_context(request: Request) -> BackupAppContext:
        return request.app.state.context

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Pydantic's default error includes input, possibly private transcript text.
        return JSONResponse(status_code=422, content={"detail": "INVALID_CONVERSATION_INPUT" if request.url.path.startswith("/v1/conversations") else "INVALID_BACKUP_INPUT"})

    @app.get("/healthz")
    def health(request: Request, context: BackupAppContext = Depends(current_context)):
        runtime = getattr(request.app.state, "runtime", None)
        conversation_runtime = getattr(request.app.state, "conversation_runtime", None)
        if (context is None or (runtime is not None and runtime.failed.is_set())
                or (conversation_runtime is not None and
                    (conversation_runtime.failed.is_set() or
                     (conversation_runtime.thread is not None and
                      not conversation_runtime.stop.is_set() and
                      not conversation_runtime.thread.is_alive())))) :
            return JSONResponse(
                status_code=503, content={"status": "worker_failed", "enabled": False}
            )
        value = {"status": "ok", "enabled": context.enabled}
        if context.private_context_enabled:
            value["private_context_enabled"] = context.private_context_store is not None
        return value

    @app.get("/v1/context-projects")
    def projects(principal: Principal = Depends(require_principal),
                 context: BackupAppContext = Depends(current_context)):
        user = context.auth_service.org_store.get_user(principal.user_id)
        if not user or user["status"] != "active" or user["org_id"] != principal.org_id:
            raise HTTPException(403, "ACCOUNT_DISABLED")
        return JSONResponse(content={"version": 1, "projects": [
            {"id": p.id, "name": p.name, "repositoryId": p.repository_id}
            for p in context.projects
            if p.org_id == principal.org_id and principal.user_id in p.allowed_users
        ]}, headers={"Cache-Control": "no-store"})

    @app.post("/v1/input-backups", status_code=202)
    def submit(
        value: BackupInput,
        principal: Principal = Depends(require_principal),
        context: BackupAppContext = Depends(current_context),
    ):
        if not context.enabled:
            raise HTTPException(503, "BACKUP_DISABLED")
        target = next(
            (t for t in context.targets if t.id == value.targetId and t.org_id == principal.org_id),
            None,
        )
        if target is None or not context.allowed_user(target, principal.user_id):
            raise HTTPException(403, "TARGET_DENIED")
        user = context.auth_service.org_store.get_user(principal.user_id)
        if not user or user["org_id"] != principal.org_id or user["status"] != "active":
            raise HTTPException(403, "ACCOUNT_DISABLED")
        try:
            existing = context.backup_store.replay(principal.org_id, principal.user_id, value)
        except BackupConflict:
            raise HTTPException(409, "EVENT_PAYLOAD_CONFLICT") from None
        if existing is not None:
            return existing
        repository_snapshot = None
        if value.projectId is not None:
            project = next((p for p in context.projects if p.id == value.projectId
                            and p.org_id == principal.org_id
                            and principal.user_id in p.allowed_users), None)
            if project is None:
                raise HTTPException(403, "PROJECT_DENIED")
            if value.repoSource != "manual" or value.repository not in {None, project.repository_id}:
                raise HTTPException(422, "INVALID_PROJECT_ATTRIBUTION")
            repository_snapshot = project.repository_id
        elif value.repoSource is not None:
            raise HTTPException(422, "INVALID_PROJECT_ATTRIBUTION")
        # Store keeps its own explicit principal/target guard.
        if principal.user_id not in target.allowed_users:
            from dataclasses import replace

            target = replace(target, allowed_users=target.allowed_users | {principal.user_id})
        user = context.auth_service.org_store.get_user(principal.user_id)
        if not user or user["org_id"] != principal.org_id or user["status"] != "active":
            raise HTTPException(403, "ACCOUNT_DISABLED")
        try:
            return context.backup_store.accept(
                principal.org_id, principal.user_id, value, target,
                sender_name=user["display_name"], repository_snapshot=repository_snapshot,
            )
        except BackupConflict:
            raise HTTPException(409, "EVENT_PAYLOAD_CONFLICT") from None
        except BackupTooLarge:
            raise HTTPException(413, "RENDERED_MESSAGE_TOO_LARGE") from None

    @app.get("/v1/input-backups/{event_id}")
    def status(
        event_id: str,
        principal: Principal = Depends(require_principal),
        context: BackupAppContext = Depends(current_context),
    ):
        value = context.backup_store.status(principal.org_id, principal.user_id, event_id)
        if value is None:
            raise HTTPException(404, "BACKUP_NOT_FOUND")
        return value

    return app
