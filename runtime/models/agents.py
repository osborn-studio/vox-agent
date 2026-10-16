"""Agent, Group, and Task models for VoxLog v3.

Gilbert is the sole owner. Everything else is an Agent or Group.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class AgentType(str, Enum):
    SYSTEM_DEFAULT = "system_default"
    USER_CREATED = "user_created"
    GROUP = "group"


class TaskStatus(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    ASSIGNED = "assigned"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


# --- Gilbert Identity ---

class OwnerIdentity(BaseModel):
    self_id: str = "gilbert"
    display_name: str = "Gilbert"
    canonical_name: str = "gilbert"
    role: str = "owner"


# --- Agent ---

class Agent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    emoji: str = "🤖"
    agent_type: AgentType = AgentType.USER_CREATED
    parent_id: str = ""  # for sub-agents
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # External binding (e.g., local 小龙虾 agent)
    external_system: str = ""  # "xiaolongxia_local"
    external_agent_ref: str = ""
    binding_status: str = ""  # "bound" | "unbound"
    can_accept_tasks: bool = False
    execution_capabilities: list[str] = Field(default_factory=list)

    # State
    is_archived: bool = False
    message_count: int = 0


# --- Group ---

class Group(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    emoji: str = "👥"
    member_agent_ids: list[str] = Field(default_factory=list)
    created_by: str = "gilbert"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_archived: bool = False


# --- Task ---

class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str = ""
    source_session_id: str = ""
    source_message_id: str = ""
    assigned_context_id: str = ""  # agent_id or group_id
    requested_by: str = "gilbert"
    status: TaskStatus = TaskStatus.DRAFT
    priority: TaskPriority = TaskPriority.MEDIUM
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Feedback
    feedback_messages: list[str] = Field(default_factory=list)  # message IDs
