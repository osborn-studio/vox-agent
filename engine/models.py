"""Core data models for the Agent Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    ASSIGNED = "assigned"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(int, Enum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    URGENT = 3


VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {TaskStatus.PLANNED, TaskStatus.CANCELLED},
    TaskStatus.PLANNED: {TaskStatus.ASSIGNED, TaskStatus.CANCELLED},
    TaskStatus.ASSIGNED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.BLOCKED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
}


@dataclass
class Task:
    agent_id: str
    title: str
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    description: str = ""
    prompt: str = ""
    status: TaskStatus = TaskStatus.DRAFT
    priority: TaskPriority = TaskPriority.MEDIUM
    trigger_message_id: str | None = None
    source_agent: str | None = None
    requester_user_id: str = ""
    workspace_id: str = ""
    working_directory: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    started_at: str | None = None
    completed_at: str | None = None

    def can_transition(self, target: TaskStatus) -> bool:
        allowed = VALID_TRANSITIONS.get(self.status, set())
        return target in allowed


# ---------------------------------------------------------------------------
# Engine Event (streaming output from agent execution)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    TEXT_DELTA = "text.delta"
    TOOL_USE = "tool.use"
    TOOL_DONE = "tool.done"
    DONE = "done"
    ERROR = "error"


@dataclass
class EngineEvent:
    type: EventType
    data: str = ""
    tool: str = ""
    input: dict[str, Any] | None = None
    output: str = ""
    result: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Task Message (persisted to tasks.db)
# ---------------------------------------------------------------------------

@dataclass
class TaskMessage:
    task_id: str
    seq: int
    author_type: str  # "user" | "agent" | "system"
    author_id: str
    type: str  # "text" | "tool_use" | "tool_result" | "status" | "error"
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    content: str = ""
    tool: str = ""
    input: dict[str, Any] | None = None
    output: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Activity Log entry (persisted to activity.db)
# ---------------------------------------------------------------------------

@dataclass
class ActivityEntry:
    actor_type: str  # "user" | "agent" | "system"
    actor_id: str
    action: str
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    target_type: str = ""
    target_id: str = ""
    details: dict[str, Any] | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
