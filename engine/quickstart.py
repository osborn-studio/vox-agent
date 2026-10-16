"""Quickstart — one-function bootstrap for the entire engine.

Provides a simple `create_engine()` function that sets up all
components with sensible defaults. Used by:
- server.py (production)
- tests (isolated instances)
- CLI tools (quick scripts)
"""

from __future__ import annotations

import logging
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

from .events import EventBus
from .store import StoreManager
from .permissions import PermissionManager, PermissionConfig, PermissionMode
from .hooks import HookChain
from .skills import SkillLoader
from .tools import ToolRegistry
from .tools.file_tools import register_file_tools
from .tools.shell_tools import register_shell_tools
from .tools.web_tools import register_web_tools
from .engine import AgentEngine
from .agent_memory import AgentMemory
from .builtin_hooks import register_builtin_hooks
from .intent import IntentParser
from .scheduler import TaskScheduler
from .orchestrator import Orchestrator
from .concurrency import ConcurrencyController
from .notifications import NotificationService
from .cost_tracker import CostTracker
from .rate_limiter import RateLimiter
from .agent_config import AgentConfigScanner
from .analytics import TaskAnalytics
from .templates import TemplateManager
from .profiles import ProfileManager
from .health import HealthChecker
from .heartbeat import HeartbeatMonitor
from .task_log import TaskLog
from .tags import TagManager
from .comments import CommentManager
from .favorites import FavoritesManager
from .search import TaskSearch
from .workspace import WorkspaceManager
from .capabilities import CapabilityManager
from .session_manager import SessionManager
from .task_history import TaskHistory
from .task_queue_stats import QueueStats
from .metrics import metrics as engine_metrics
from .audit_trail import AuditTrail
from .config_validator import ConfigValidator
from .conversation import ConversationManager
from .plugins import PluginLoader
from .recurring import RecurringScheduler
from .timeout import TimeoutManager
from .diff import TaskDiff
from .snapshot import SnapshotManager
from .batch import BatchOperations
from .dependencies import DependencyManager
from .replay import EventReplay
from .export import ReportExporter
from .obsidian_export import ObsidianTaskExporter
from .webhooks import WebhookHandler
from .chaining import AgentChain
from .agent_router import AgentRouter
from .dashboard import DashboardAggregator

logger = logging.getLogger(__name__)


class EngineInstance:
    """All engine components in one object."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def create_engine(
    data_dir: Path | None = None,
    permission_mode: PermissionMode = PermissionMode.DEFAULT,
    global_max_tasks: int = 10,
) -> EngineInstance:
    """Bootstrap the entire engine with sensible defaults.

    Returns an EngineInstance with all components accessible as attributes.

    Usage:
        engine = create_engine()
        task = await engine.scheduler.create_task(...)
        engine.store.close()
    """
    data_dir = data_dir or resolve_vox_agent_data_dir()

    # Core
    event_bus = EventBus()
    store = StoreManager(data_dir)
    store.connect()

    # Tools
    tool_registry = ToolRegistry()
    register_file_tools(tool_registry)
    register_shell_tools(tool_registry)
    register_web_tools(tool_registry)

    # Security
    permission_mgr = PermissionManager(PermissionConfig(mode=permission_mode))
    hook_chain = HookChain()

    # Skills & Memory
    skill_loader = SkillLoader(data_dir / "skills")
    agent_memory = AgentMemory(data_dir / "memory")
    register_builtin_hooks(hook_chain, store, agent_memory)

    # Engine
    agent_engine = AgentEngine(tool_registry, permission_mgr, hook_chain, skill_loader, agent_memory)
    concurrency = ConcurrencyController(global_max=global_max_tasks)
    scheduler = TaskScheduler(event_bus, agent_engine, store, concurrency)

    # Intelligence
    intent_parser = IntentParser()
    orchestrator = Orchestrator(intent_parser, scheduler, event_bus, store)

    # Observability
    analytics = TaskAnalytics(store)
    cost_tracker = CostTracker(data_dir / "costs")
    health_checker = HealthChecker(store, agent_memory, skill_loader, tool_registry)
    heartbeat = HeartbeatMonitor(event_bus)
    notification_svc = NotificationService(event_bus)

    # Management
    agent_scanner = AgentConfigScanner()
    template_mgr = TemplateManager(data_dir / "templates")
    profile_mgr = ProfileManager(data_dir / "profiles")
    tag_mgr = TagManager(store)
    comment_mgr = CommentManager(store)
    favorites_mgr = FavoritesManager(store)
    search = TaskSearch(store)
    capability_mgr = CapabilityManager()
    session_mgr = SessionManager(store, event_bus)
    task_history = TaskHistory(store)
    queue_stats = QueueStats(store)
    audit = AuditTrail(store)
    task_log = TaskLog(data_dir / "logs")
    workspace_mgr = WorkspaceManager(data_dir / "workspaces")
    conversation_mgr = ConversationManager()
    plugin_loader = PluginLoader(data_dir / "plugins")
    recurring = RecurringScheduler(data_dir / "schedules.json")
    timeout_mgr = TimeoutManager(store, event_bus)
    rate_limiter = RateLimiter()
    config_validator = ConfigValidator()

    # Integration
    deps = DependencyManager(store)
    batch = BatchOperations(scheduler, deps)
    diff = TaskDiff(store)
    snapshot_mgr = SnapshotManager(
        store,
        agent_memory,
        skill_loader,
        agent_scanner,
        data_dir / "snapshots",
    )
    replay = EventReplay(store, event_bus)
    exporter = ReportExporter(store)
    obsidian = ObsidianTaskExporter(store)
    webhook_handler = WebhookHandler()
    chain = AgentChain(scheduler, store, event_bus)
    router = AgentRouter(agent_scanner, heartbeat, analytics)
    dashboard = DashboardAggregator(
        store, analytics, queue_stats, health_checker,
        cost_tracker, agent_memory, skill_loader, tool_registry, heartbeat,
    )

    logger.info(
        "engine created: %d tools, %d components",
        len(tool_registry.list_tools()), 40,
    )

    return EngineInstance(
        event_bus=event_bus, store=store, tool_registry=tool_registry,
        permission_mgr=permission_mgr, hook_chain=hook_chain,
        skill_loader=skill_loader, agent_memory=agent_memory,
        agent_engine=agent_engine, scheduler=scheduler,
        intent_parser=intent_parser, orchestrator=orchestrator,
        analytics=analytics, cost_tracker=cost_tracker,
        health_checker=health_checker, heartbeat=heartbeat,
        notification_svc=notification_svc,
        agent_scanner=agent_scanner, template_mgr=template_mgr,
        profile_mgr=profile_mgr, tag_mgr=tag_mgr,
        comment_mgr=comment_mgr, favorites_mgr=favorites_mgr,
        search=search, capability_mgr=capability_mgr,
        session_mgr=session_mgr, task_history=task_history,
        queue_stats=queue_stats, audit=audit, task_log=task_log,
        workspace_mgr=workspace_mgr, conversation_mgr=conversation_mgr,
        plugin_loader=plugin_loader, recurring=recurring,
        timeout_mgr=timeout_mgr, rate_limiter=rate_limiter,
        config_validator=config_validator,
        batch=batch, diff=diff, snapshot_mgr=snapshot_mgr,
        replay=replay, exporter=exporter, obsidian=obsidian,
        webhook_handler=webhook_handler, chain=chain,
        router=router, dashboard=dashboard,
        metrics=engine_metrics, concurrency=concurrency,
        deps=deps,
    )
