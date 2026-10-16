"""Composition of HTTP-facing services owned by one application context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..agent_groups import GroupManager
from ..agent_router import AgentRouter
from ..analytics import TaskAnalytics
from ..annotations import AnnotationManager
from ..audit_trail import AuditTrail
from ..batch import BatchOperations
from ..capabilities import CapabilityManager
from ..comments import CommentManager
from ..config_validator import ConfigValidator
from ..conversation import ConversationManager
from ..cost_tracker import CostTracker
from ..dashboard import DashboardAggregator
from ..dependencies import DependencyManager
from ..diff import TaskDiff
from ..export import ReportExporter
from ..favorites import FavoritesManager
from ..health import HealthChecker
from ..heartbeat import HeartbeatMonitor
from ..notifications import NotificationService
from ..obsidian_export import ObsidianTaskExporter
from ..openclaw_bridge import OpenClawBridge
from ..plugins import PluginLoader
from ..profiles import ProfileManager
from ..rate_limiter import RateLimiter
from ..recurring import RecurringSchedule, RecurringScheduler
from ..replay import EventReplay
from ..search import TaskSearch
from ..session_manager import SessionManager
from ..tags import TagManager
from ..task_history import TaskHistory
from ..task_log import TaskLog
from ..task_queue_stats import QueueStats
from ..templates import TemplateManager
from ..terminal_bridge import TerminalBridge
from ..timeout import TimeoutManager
from ..webhooks import WebhookHandler
from ..workspace import WorkspaceManager

if TYPE_CHECKING:
    from ..app_context import AppContext


@dataclass(slots=True)
class ApiServices:
    """Peripheral services shared by API routers and lifecycle hooks."""

    task_exporter: ObsidianTaskExporter
    task_analytics: TaskAnalytics
    template_manager: TemplateManager
    dependency_manager: DependencyManager
    batch_operations: BatchOperations
    notification_service: NotificationService
    task_search: TaskSearch
    health_checker: HealthChecker
    recurring_scheduler: RecurringScheduler
    heartbeat_monitor: HeartbeatMonitor
    webhook_handler: WebhookHandler
    cost_tracker: CostTracker
    rate_limiter: RateLimiter
    task_log: TaskLog
    workspace_manager: WorkspaceManager
    profile_manager: ProfileManager
    report_exporter: ReportExporter
    config_validator: ConfigValidator
    conversation_manager: ConversationManager
    plugin_loader: PluginLoader
    agent_router: AgentRouter
    tag_manager: TagManager
    comment_manager: CommentManager
    audit_trail: AuditTrail
    timeout_manager: TimeoutManager
    task_diff: TaskDiff
    favorites_manager: FavoritesManager
    task_history: TaskHistory
    queue_stats: QueueStats
    session_manager: SessionManager
    capability_manager: CapabilityManager
    event_replay: EventReplay
    group_manager: GroupManager
    annotation_manager: AnnotationManager
    terminal_bridge: TerminalBridge
    openclaw_gateway: OpenClawBridge
    dashboard: DashboardAggregator


def create_api_services(context: AppContext) -> ApiServices:
    """Build every HTTP-facing service against this context's core objects."""

    store = context.store
    event_bus = context.event_bus
    analytics = TaskAnalytics(store)
    dependencies = DependencyManager(store)
    batch = BatchOperations(context.scheduler, dependencies)
    health = HealthChecker(
        store,
        context.agent_memory,
        context.skill_loader,
        context.tool_registry,
    )
    recurring = RecurringScheduler()

    async def on_recurring_trigger(schedule: RecurringSchedule) -> None:
        await context.scheduler.create_task(
            agent_id=schedule.agent_id,
            title=f"[Scheduled] {schedule.name}",
            prompt=schedule.prompt,
            provider=schedule.provider,
        )

    recurring.set_trigger(on_recurring_trigger)
    heartbeat = HeartbeatMonitor(event_bus)
    cost_tracker = CostTracker()
    queue_stats = QueueStats(store)
    task_search = TaskSearch(store)
    agent_router = AgentRouter(context.agent_scanner, heartbeat, analytics)
    dashboard = DashboardAggregator(
        store,
        analytics,
        queue_stats,
        health,
        cost_tracker,
        context.agent_memory,
        context.skill_loader,
        context.tool_registry,
        heartbeat,
    )

    return ApiServices(
        task_exporter=ObsidianTaskExporter(store),
        task_analytics=analytics,
        template_manager=TemplateManager(),
        dependency_manager=dependencies,
        batch_operations=batch,
        notification_service=NotificationService(event_bus),
        task_search=task_search,
        health_checker=health,
        recurring_scheduler=recurring,
        heartbeat_monitor=heartbeat,
        webhook_handler=WebhookHandler(),
        cost_tracker=cost_tracker,
        rate_limiter=RateLimiter(),
        task_log=TaskLog(),
        workspace_manager=WorkspaceManager(),
        profile_manager=ProfileManager(),
        report_exporter=ReportExporter(store),
        config_validator=ConfigValidator(),
        conversation_manager=ConversationManager(),
        plugin_loader=PluginLoader(),
        agent_router=agent_router,
        tag_manager=TagManager(store),
        comment_manager=CommentManager(store),
        audit_trail=AuditTrail(store),
        timeout_manager=TimeoutManager(store, event_bus),
        task_diff=TaskDiff(store),
        favorites_manager=FavoritesManager(store),
        task_history=TaskHistory(store),
        queue_stats=queue_stats,
        session_manager=SessionManager(store, event_bus),
        capability_manager=CapabilityManager(),
        event_replay=EventReplay(store, event_bus),
        group_manager=GroupManager(),
        annotation_manager=AnnotationManager(store),
        terminal_bridge=TerminalBridge(),
        openclaw_gateway=OpenClawBridge(),
        dashboard=dashboard,
    )
