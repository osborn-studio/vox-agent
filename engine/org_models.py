"""Organization collaboration identity models for Vox Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


def _new_id() -> str:
    return uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrgRole(str, Enum):
    MEMBER = "member"
    PRODUCT_MANAGER = "product_manager"
    TECH_LEAD = "tech_lead"
    ENGINEERING_MANAGER = "engineering_manager"
    ADMIN = "admin"


class TeamRole(str, Enum):
    MEMBER = "member"
    LEAD = "lead"
    MANAGER = "manager"


class ProjectRole(str, Enum):
    MEMBER = "member"
    LEAD = "lead"
    MANAGER = "manager"


class AgentVisibilityScope(str, Enum):
    PRIVATE = "private"
    TEAM = "team"
    ORG = "org"


@dataclass
class Organization:
    name: str
    id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)


@dataclass
class User:
    org_id: str
    email: str
    display_name: str
    role: OrgRole = OrgRole.MEMBER
    status: str = "active"
    id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)


@dataclass
class Team:
    org_id: str
    name: str
    id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)


@dataclass
class TeamMember:
    team_id: str
    user_id: str
    role: TeamRole = TeamRole.MEMBER
    created_at: str = field(default_factory=_now_iso)


@dataclass
class Project:
    org_id: str
    name: str
    team_id: str | None = None
    id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)


@dataclass
class ProjectMember:
    project_id: str
    user_id: str
    role: ProjectRole = ProjectRole.MEMBER
    created_at: str = field(default_factory=_now_iso)


@dataclass
class AgentIdentity:
    org_id: str
    provider: str
    display_name: str
    owner_user_id: str | None = None
    visibility_scope: AgentVisibilityScope = AgentVisibilityScope.TEAM
    id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)


@dataclass
class RequestContext:
    org_id: str
    requester_user_id: str
    team_id: str | None = None
    project_id: str | None = None
    purpose: str = ""
