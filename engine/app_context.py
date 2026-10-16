"""Application-owned service composition for the Vox Agents engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from runtime.data_paths import resolve_vox_agent_data_dir

from .agent_config import AgentConfigScanner
from .agent_memory import AgentMemory
from .access_audit import AccessAuditStore
from .auth import AuthService
from .builtin_hooks import register_builtin_hooks
from .conversation_sessions import ConversationSessionBroker
from .device_simulator import DeviceSimulatorRepository
from .engine import AgentEngine
from .events import EventBus
from .hooks import HookChain
from .intent import IntentParser
from .model_policy import ModelPolicy
from .orchestrator import Orchestrator
from .org_migration import migrate_org_pilot_data
from .org_store import OrgStore
from .permissioned_retrieval import PermissionedRetriever
from .permissions import PermissionManager
from .policy import PolicyEngine
from .policy_store import PolicyStore
from .retention import RetentionScheduler, RetentionSweeper
from .retention_policy import RetentionPolicy
from .scheduler import TaskScheduler
from .skills import SkillLoader
from .speech_asr import SpeechAsrProcessor
from .streaming_speech import SpeechStreamBroker
from .store import StoreManager
from .tools import ToolRegistry
from .tools.file_tools import register_file_tools
from .tools.shell_tools import register_shell_tools
from .tools.web_tools import register_web_tools
from .translation import TranslationPipeline
from .translation_provider import get_translator
from .voice_audit import VoiceAuditLog
from .voice_memory import TokenLedger, VoiceMemoryRepository
from .work_events import WorkEventStore

_FROM_ENV = object()

if TYPE_CHECKING:
    from .api.services import ApiServices


@dataclass(slots=True)
class AppContext:
    """Core services owned by one application instance."""

    event_bus: EventBus
    store: StoreManager
    tool_registry: ToolRegistry
    permission_manager: PermissionManager
    hook_chain: HookChain
    skill_loader: SkillLoader
    agent_memory: AgentMemory
    agent_engine: AgentEngine
    scheduler: TaskScheduler
    intent_parser: IntentParser
    agent_scanner: AgentConfigScanner
    orchestrator: Orchestrator
    voice_memory_repo: Any
    device_simulator_repo: DeviceSimulatorRepository
    token_ledger: TokenLedger
    voice_audit_log: VoiceAuditLog
    org_store: OrgStore
    org_event_store: WorkEventStore
    org_access_audit_store: AccessAuditStore
    org_policy: PolicyEngine
    org_retriever: PermissionedRetriever
    org_migration_result: dict[str, Any]
    auth_service: AuthService
    model_policy: ModelPolicy
    retention_policy: RetentionPolicy
    policy_store: PolicyStore
    retention_sweeper: RetentionSweeper
    retention_scheduler: RetentionScheduler
    speech_stream_broker: SpeechStreamBroker
    speech_asr_processor: SpeechAsrProcessor
    conversation_session_broker: ConversationSessionBroker
    translation_pipeline: TranslationPipeline
    api: ApiServices = field(init=False)

    def close(self) -> None:
        close_voice_repo = getattr(self.voice_memory_repo, "close", None)
        if callable(close_voice_repo):
            close_voice_repo()
        self.voice_audit_log.close()
        self.org_access_audit_store.close()
        self.org_event_store.close()
        self.org_store.close()
        self.policy_store.close()
        self.store.close()


def _voice_memory_repository(db_path: str | Path | None | object):
    resolved = os.environ.get("VOXAGENT_VOICE_MEMORY_DB") if db_path is _FROM_ENV else db_path
    if resolved:
        from .voice_memory_sqlite import SqliteVoiceMemoryRepository

        return SqliteVoiceMemoryRepository(resolved)
    return VoiceMemoryRepository()


def _retention_interval_seconds() -> float:
    try:
        hours = float(os.environ.get("VOXAGENT_RETENTION_SWEEP_HOURS", "24"))
    except ValueError:
        hours = 24
    return max(hours, 1 / 60) * 3600


def create_app_context(
    *,
    data_dir: Path | None = None,
    voice_memory_db: str | Path | None | object = _FROM_ENV,
    use_terminal_bridge: bool = True,
    edition: str | None = None,
    bootstrap_admin_token: str | None | object = _FROM_ENV,
) -> AppContext:
    """Build an isolated set of core services without connecting the task store."""

    if data_dir is None and os.environ.get("VOXAGENT_DATA_DIR"):
        data_dir = Path(os.environ["VOXAGENT_DATA_DIR"]).expanduser()
    event_bus = EventBus()
    store = StoreManager(data_dir)
    tool_registry = ToolRegistry()

    async def publish_permission_request(request) -> None:
        await event_bus.emit("permission:request", request)

    permission_manager = PermissionManager(
        on_request=publish_permission_request,
        rules_path=store.data_dir / "permission_rules.json",
    )
    hook_chain = HookChain()
    skill_loader = SkillLoader(store.data_dir / "skills")

    register_file_tools(tool_registry)
    register_shell_tools(tool_registry)
    register_web_tools(tool_registry)

    agent_memory = AgentMemory(store.data_dir / "memory")
    agent_engine = AgentEngine(
        tool_registry,
        permission_manager,
        hook_chain,
        skill_loader,
        agent_memory,
    )
    register_builtin_hooks(hook_chain, store, agent_memory)

    scheduler = TaskScheduler(event_bus, agent_engine, store)
    intent_parser = IntentParser()
    agent_scanner = AgentConfigScanner()
    orchestrator = Orchestrator(
        intent_parser,
        scheduler,
        event_bus,
        store,
        use_terminal_bridge=use_terminal_bridge,
    )
    voice_audit_log = VoiceAuditLog(store.data_dir / "admin_audit.db")
    org_migration_result = migrate_org_pilot_data(store.data_dir, dry_run=False)
    org_store = OrgStore(store.data_dir)
    org_event_store = WorkEventStore(store.data_dir)
    org_access_audit_store = AccessAuditStore(store.data_dir)
    org_policy = PolicyEngine(
        org_store=org_store,
        audit_store=org_access_audit_store,
    )
    org_retriever = PermissionedRetriever(
        event_store=org_event_store,
        policy=org_policy,
    )
    resolved_token = (
        os.environ.get("VOXAGENT_BOOTSTRAP_ADMIN_TOKEN")
        if bootstrap_admin_token is _FROM_ENV
        else bootstrap_admin_token
    )
    auth_service = AuthService(
        org_store=org_store,
        audit_log=voice_audit_log,
        edition=edition or os.environ.get("VOXAGENT_EDITION", "personal"),
        bootstrap_admin_token=resolved_token,
    )
    voice_memory_repo = _voice_memory_repository(voice_memory_db)
    policy_store = PolicyStore(store.data_dir / "admin_state.db")
    model_policy = policy_store.load_model_policy()
    retention_policy = policy_store.load_retention_policy()
    audio_dir = Path(
        os.environ.get(
            "VOXAGENT_AUDIO_DIR", str(resolve_vox_agent_data_dir() / "audio")
        )
    )
    retention_sweeper = RetentionSweeper(
        repository=voice_memory_repo,
        policy=retention_policy,
        audit_log=voice_audit_log,
        state_store=policy_store,
        allowed_audio_dirs=[audio_dir],
    )

    speech_stream_broker = SpeechStreamBroker()
    context = AppContext(
        event_bus=event_bus,
        store=store,
        tool_registry=tool_registry,
        permission_manager=permission_manager,
        hook_chain=hook_chain,
        skill_loader=skill_loader,
        agent_memory=agent_memory,
        agent_engine=agent_engine,
        scheduler=scheduler,
        intent_parser=intent_parser,
        agent_scanner=agent_scanner,
        orchestrator=orchestrator,
        voice_memory_repo=voice_memory_repo,
        device_simulator_repo=DeviceSimulatorRepository(),
        token_ledger=TokenLedger(),
        voice_audit_log=voice_audit_log,
        org_store=org_store,
        org_event_store=org_event_store,
        org_access_audit_store=org_access_audit_store,
        org_policy=org_policy,
        org_retriever=org_retriever,
        org_migration_result=org_migration_result,
        auth_service=auth_service,
        model_policy=model_policy,
        retention_policy=retention_policy,
        policy_store=policy_store,
        retention_sweeper=retention_sweeper,
        retention_scheduler=RetentionScheduler(
            retention_sweeper,
            interval_seconds=_retention_interval_seconds(),
        ),
        speech_stream_broker=speech_stream_broker,
        speech_asr_processor=SpeechAsrProcessor(speech_stream_broker),
        conversation_session_broker=ConversationSessionBroker(),
        translation_pipeline=TranslationPipeline(get_translator()),
    )
    from .api.services import create_api_services

    context.api = create_api_services(context)
    return context
