"""Vox Agents FastAPI composition root."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.admin import router as admin_router
from .api.capture import create_router as create_capture_router
from .api.core import create_router as create_core_router
from .api.conversation_sessions import create_router as create_conversation_router
from .api.integrations import create_router as create_integrations_router
from .api.memory import create_router as create_memory_router
from .api.org import create_router as create_org_router
from .api.permissions import create_router as create_permissions_router
from .api.speech import create_router as create_speech_router
from .api.tasks import create_router as create_tasks_router
from .api.translation import create_router as create_translation_router
from .app_context import AppContext, create_app_context
from .auth import require_admin, require_principal
from .local_auth import LocalTokenGuard, launch_secret
from .middleware import RequestLoggingMiddleware

logger = logging.getLogger(__name__)


def _cors_origins() -> list[str]:
    raw = os.environ.get("VOXAGENT_CORS_ORIGINS")
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:5273",
        "http://127.0.0.1:5273",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def create_app(context: AppContext | None = None, *, local_token: str | None = None) -> FastAPI:
    """Create an isolated application and bind every route to its context.

    ``local_token`` is the per-launch secret (T29). When not given, it is read
    from stdin if ``VOXAGENT_LOCAL_TOKEN_SOURCE=stdin``; tests pass it directly.
    """

    app_context = context or create_app_context()
    services = app_context.api

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        validation = services.config_validator.validate()
        for warning in validation.warnings:
            logger.warning("config: %s", warning)

        app_context.store._ensure_connected()
        skills = app_context.skill_loader.load_all()
        plugins = services.plugin_loader.load_all()
        schedules = services.recurring_scheduler.start_all()
        services.timeout_manager.start()
        services.session_manager.start()
        app_context.retention_scheduler.start()
        logger.info(
            "engine ready: %d tools, %d skills, %d plugins, %d schedules",
            len(app_context.tool_registry.list_tools()),
            len(skills),
            len(plugins),
            schedules,
        )
        try:
            yield
        finally:
            await app_context.retention_scheduler.stop()
            services.session_manager.end()
            services.timeout_manager.stop()
            services.recurring_scheduler.stop_all()
            app_context.close()

    application = FastAPI(
        title="Vox Agent Engine",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.context = app_context
    application.add_middleware(RequestLoggingMiddleware)

    origins = _cors_origins()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # T29: a desktop-launched engine admits only its launcher. Added last so
    # it is the outermost layer, ahead of CORS and every route.
    if local_token is None:
        local_token = launch_secret()
    if local_token is not None:
        application.add_middleware(LocalTokenGuard, token=local_token)

    @application.get("/health", tags=["health"])
    async def health():
        return {
            "status": "ok",
            "active_tasks": len(app_context.agent_engine._processes),
        }

    # Route order is intentional: specific memory paths precede the legacy
    # /v1/memory/{agent_id} catch-all inside the memory router.
    admin_dependencies = [Depends(require_admin)]
    application.include_router(
        create_core_router(app_context),
        dependencies=admin_dependencies,
    )
    # Memory exposes principal-scoped product reads alongside explicitly
    # admin-only mutation, token, legacy, and device routes.
    application.include_router(create_memory_router(app_context))
    application.include_router(create_permissions_router(app_context))
    application.include_router(
        create_tasks_router(app_context),
        dependencies=admin_dependencies,
    )
    application.include_router(
        create_integrations_router(app_context),
        dependencies=admin_dependencies,
    )
    application.include_router(create_capture_router(app_context))
    application.include_router(create_org_router(app_context))
    application.include_router(
        create_speech_router(app_context),
        dependencies=[Depends(require_principal)],
    )
    application.include_router(
        create_translation_router(app_context),
        dependencies=[Depends(require_principal)],
    )
    # Host routes declare principal dependencies internally; the three public
    # routes authenticate only with short-lived, session-scoped capabilities.
    application.include_router(create_conversation_router(app_context))
    application.include_router(admin_router)
    return application


app = create_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=0,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
