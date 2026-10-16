"""Explicit opt-in, single-host backup service runtime. No CLI credential scraping."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .context_projects import ContextProject
from .auth import AuthService
from .backup_app import BackupAppContext, ConversationPolicy, create_backup_app
from .input_backup import BackupStore, Target
from .input_backup_worker import BackupWorker, FeishuTransport
from .org_store import OrgStore
from .voice_audit import VoiceAuditLog
from .feishu_login import FeishuLoginSettings, FeishuLoginService, FeishuIdentityProvider


class TargetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)
    org_id: str = Field(min_length=1)
    chat_id: str = Field(pattern=r"^oc_[a-zA-Z0-9]+$")
    allowed_users: list[str] = Field(min_length=1)


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    data_dir: str = Field(min_length=1)
    app_id: str = Field(pattern=r"^cli_[a-zA-Z0-9]+$")
    app_secret_ref: str = Field(min_length=1)
    bootstrap_secret_ref: str | None = None
    enabled: bool = False
    conversations_enabled: bool = False
    private_context_enabled: bool = False
    conversation_policy: ConversationPolicy = Field(default_factory=ConversationPolicy)
    targets: list[TargetSettings] = Field(default_factory=list)
    projects: list[ContextProject] = Field(default_factory=list, max_length=1000)
    feishu_login: FeishuLoginSettings | None = None

    @model_validator(mode="after")
    def conversation_identity_boundary(self):
        if len({(p.org_id, p.id) for p in self.projects}) != len(self.projects):
            raise ValueError("duplicate project configuration")
        if self.conversations_enabled and (
            self.feishu_login is None or len(self.feishu_login.tenant_orgs) != 1
        ):
            raise ValueError("conversations require exactly one Feishu tenant")
        return self


def read_keyring_secret(reference: str) -> str:
    """References are account names in the dedicated OS keyring service."""
    try:
        import keyring

        # Reject plaintext/fallback backends; deployment must select an OS vault.
        backend = keyring.get_keyring()
        if type(backend).__module__ not in {
            "keyring.backends.macOS",
            "keyring.backends.SecretService",
            "keyring.backends.Windows",
            "keyring.backends.kwallet",
        }:
            raise RuntimeError("unsupported keyring backend")
        secret = keyring.get_password("vox-agent-backup", reference)
        if not secret:
            raise RuntimeError("missing credential")
        return secret
    except Exception:
        raise RuntimeError("BACKUP_SECRET_UNAVAILABLE") from None


class RuntimeAuthService(AuthService):
    """Serialize request authentication with worker identity reads."""

    def __init__(self, **kwargs):
        self.identity_lock = threading.RLock()
        self.feishu_login = None
        super().__init__(**kwargs)

    def authenticate(self, token):
        with self.identity_lock:
            if token.startswith("vox_sso_"):
                return self.feishu_login.authenticate(token) if self.feishu_login else None
            return super().authenticate(token)


class WorkerRuntime:
    def __init__(self):
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.failed = threading.Event()
        self.thread = None

    @property
    def worker_alive(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, context, transport):
        def authorize(job):
            with context.auth_service.identity_lock:
                return context.authorize_delivery(job)

        def run():
            try:
                with BackupWorker(context.backup_store, transport, authorize=authorize) as worker:
                    self.ready.set()
                    while not self.stop.is_set():
                        worker.run_once()
                        self.stop.wait(0.25)
            except Exception:
                # Do not retain an exception/traceback that may contain a job body.
                context.enabled = False
                self.failed.set()
                self.ready.set()

        self.thread = threading.Thread(target=run, name="vox-backup-worker", daemon=False)
        self.thread.start()
        self.ready.wait()
        if self.failed.is_set():
            self.thread.join()
            raise RuntimeError("BACKUP_WORKER_START_FAILED")

    def close(self):
        self.stop.set()
        if self.thread is not None:
            # Finish the in-flight bounded HTTP attempt before closing SQLite/client.
            self.thread.join()


def create_runtime_app(
    settings: RuntimeSettings, *, read_secret=read_keyring_secret, transport_factory=None,
    conversation_transport_factory=None
):
    runtime = WorkerRuntime()

    @asynccontextmanager
    async def lifespan(app):
        runtime = WorkerRuntime()
        app.state.runtime = runtime
        from .feishu_conversation_worker import ConversationRuntime
        conversation_runtime = ConversationRuntime()
        app.state.conversation_runtime = conversation_runtime
        with ExitStack() as resources:
            root = Path(settings.data_dir).expanduser().resolve()
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if root.stat().st_mode & 0o077 or root.stat().st_uid != os.getuid():
                raise RuntimeError("BACKUP_DATA_DIRECTORY_MUST_BE_PRIVATE")
            org = OrgStore(root / "identity")
            resources.callback(org.close)
            audit = VoiceAuditLog(root / "identity-audit.sqlite")
            resources.callback(audit.close)
            bootstrap = None
            if org.count_active_credentials() == 0 and settings.bootstrap_secret_ref:
                bootstrap = read_secret(settings.bootstrap_secret_ref)
            auth = RuntimeAuthService(
                org_store=org,
                audit_log=audit,
                edition="enterprise",
                bootstrap_admin_token=bootstrap,
            )
            store = BackupStore(root / "input-backup.sqlite")
            resources.callback(store.close)
            targets = tuple(
                Target(t.id, t.version, t.org_id, t.chat_id, frozenset(t.allowed_users))
                for t in settings.targets
            )
            context = BackupAppContext(
                auth,
                store,
                targets,
                enabled=settings.enabled,
                conversations_enabled=settings.conversations_enabled,
                private_context_enabled=settings.private_context_enabled,
                conversation_policy=settings.conversation_policy,
                projects=tuple(settings.projects),
            )
            app.state.context = context
            client = resources.enter_context(httpx.Client(trust_env=False))
            if settings.feishu_login:
                login = FeishuLoginService(
                    settings.feishu_login,
                    settings.app_id,
                    auth,
                    root / "feishu-login.sqlite",
                    FeishuIdentityProvider(
                        settings.app_id, lambda: read_secret(settings.app_secret_ref), client
                    ),
                )
                resources.callback(login.close)
                auth.feishu_login = login
                app.state.feishu_login = login
            if settings.conversations_enabled:
                from .feishu_client import FeishuClient, SharedFeishuBackupTransport
                from .feishu_conversations import ConversationDiscovery, ConversationMappings

                mappings = ConversationMappings(root / "conversation-mappings.sqlite")
                resources.callback(mappings.close)
                discovery_client = FeishuClient(
                    settings.app_id, next(iter(settings.feishu_login.tenant_orgs)),
                    lambda: read_secret(settings.app_secret_ref),
                    transport=conversation_transport_factory() if conversation_transport_factory else None,
                )
                resources.callback(discovery_client.close)
                context.conversation_discovery = ConversationDiscovery(discovery_client, mappings)
                from .feishu_conversation_store import ConversationStore
                conversation_store = ConversationStore(root / "conversation-outbox.sqlite")
                resources.callback(conversation_store.close)
                context.conversation_store = conversation_store
                from .feishu_conversation_reading import ConversationReading
                context.conversation_reading = ConversationReading(context)
            try:
                if settings.conversations_enabled:
                    await asyncio.to_thread(conversation_runtime.start, context)
                if settings.enabled:
                    # Validate protected credential availability at startup, not on
                    # the first employee's submission. Never store it in config.
                    read_secret(settings.app_secret_ref)
                    transport = (
                        transport_factory(client, lambda: read_secret(settings.app_secret_ref))
                        if transport_factory
                        else SharedFeishuBackupTransport(discovery_client)
                        if settings.conversations_enabled
                        else FeishuTransport(
                            settings.app_id,
                            lambda: read_secret(settings.app_secret_ref),
                            client=client,
                        )
                    )
                    await asyncio.to_thread(runtime.start, context, transport)
                yield
            finally:
                await asyncio.to_thread(conversation_runtime.close)
                await asyncio.to_thread(runtime.close)
                app.state.context = None
                app.state.feishu_login = None

    app = create_backup_app(
        lifespan=lifespan, enable_feishu_login=settings.feishu_login is not None,
        enable_conversations=settings.conversations_enabled,
        enable_private_context=settings.private_context_enabled
    )
    app.state.runtime = runtime
    return app


def main():
    parser = argparse.ArgumentParser(
        description="Isolated Vox input backup service (single process)"
    )
    parser.add_argument(
        "--config", required=True, type=Path, help="JSON with secret references only"
    )
    parser.add_argument("--host", choices=["127.0.0.1", "::1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    try:
        settings = RuntimeSettings.model_validate(json.loads(args.config.read_text()))
    except Exception:
        parser.exit(2, "Invalid backup configuration; values omitted.\n")
    import uvicorn

    uvicorn.run(
        create_runtime_app(settings),
        host=args.host,
        port=args.port,
        workers=1,
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
