"""Repeatable demo seed data for the organization collaboration pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .org_models import (
    AgentIdentity,
    AgentVisibilityScope,
    Organization,
    OrgRole,
    Team,
    TeamMember,
    TeamRole,
    User,
)
from .org_store import OrgStore
from .work_events import (
    RetentionClass,
    WorkEvent,
    WorkEventSourceType,
    WorkEventStore,
    WorkEventVisibility,
)


DEMO_ORG_ID = "demo_org_vox"
DEMO_TEAM_ID = "demo_team_platform"
DEMO_PROJECT_ID = "demo_project_checkout"
DEMO_WORKSPACE_ID = "demo_workspace_voxagent"
DEMO_DAY = "2026-05-17"
DEMO_PRIVATE_NOTE_BODY = "private roadmap note: acquisition concern stays private"


def seed_org_demo_data(data_dir: Path | str) -> dict[str, Any]:
    """Create or reuse a deterministic org-collab pilot demo dataset."""

    data_path = Path(data_dir)
    org_store = OrgStore(data_path)
    event_store = WorkEventStore(data_path)
    org_store.connect()
    event_store.connect()

    try:
        _seed_identity(org_store)
        _seed_events(event_store)
        return _seed_result()
    finally:
        event_store.close()
        org_store.close()


def _seed_identity(store: OrgStore) -> None:
    org = Organization(
        id=DEMO_ORG_ID,
        name="Vox Agent",
        created_at=f"{DEMO_DAY}T09:00:00+00:00",
    )
    if not store.get_organization(org.id):
        store.insert_organization(org)

    users = [
        User(
            id="demo_user_pm",
            org_id=DEMO_ORG_ID,
            email="pm@voxagent.local",
            display_name="Priya PM",
            role=OrgRole.PRODUCT_MANAGER,
            created_at=f"{DEMO_DAY}T09:01:00+00:00",
        ),
        User(
            id="demo_user_manager",
            org_id=DEMO_ORG_ID,
            email="manager@voxagent.local",
            display_name="Morgan Manager",
            role=OrgRole.ENGINEERING_MANAGER,
            created_at=f"{DEMO_DAY}T09:02:00+00:00",
        ),
        User(
            id="demo_user_alice",
            org_id=DEMO_ORG_ID,
            email="alice.dev@voxagent.local",
            display_name="Alice Developer",
            role=OrgRole.MEMBER,
            created_at=f"{DEMO_DAY}T09:03:00+00:00",
        ),
        User(
            id="demo_user_bob",
            org_id=DEMO_ORG_ID,
            email="bob.dev@voxagent.local",
            display_name="Bob Developer",
            role=OrgRole.MEMBER,
            created_at=f"{DEMO_DAY}T09:04:00+00:00",
        ),
    ]
    for user in users:
        if not store.get_user(user.id):
            store.insert_user(user)

    team = Team(
        id=DEMO_TEAM_ID,
        org_id=DEMO_ORG_ID,
        name="Platform Pilot",
        created_at=f"{DEMO_DAY}T09:05:00+00:00",
    )
    if not store.get_team(team.id):
        store.insert_team(team)

    members = [
        TeamMember(
            team_id=DEMO_TEAM_ID,
            user_id="demo_user_pm",
            role=TeamRole.MEMBER,
            created_at=f"{DEMO_DAY}T09:06:00+00:00",
        ),
        TeamMember(
            team_id=DEMO_TEAM_ID,
            user_id="demo_user_manager",
            role=TeamRole.MANAGER,
            created_at=f"{DEMO_DAY}T09:07:00+00:00",
        ),
        TeamMember(
            team_id=DEMO_TEAM_ID,
            user_id="demo_user_alice",
            role=TeamRole.LEAD,
            created_at=f"{DEMO_DAY}T09:08:00+00:00",
        ),
        TeamMember(
            team_id=DEMO_TEAM_ID,
            user_id="demo_user_bob",
            role=TeamRole.MEMBER,
            created_at=f"{DEMO_DAY}T09:09:00+00:00",
        ),
    ]
    existing_members = {
        member["user_id"] for member in store.list_team_members(DEMO_TEAM_ID)
    }
    for member in members:
        if member.user_id not in existing_members:
            store.insert_team_member(member)

    agents = [
        AgentIdentity(
            id="demo_agent_codex",
            org_id=DEMO_ORG_ID,
            provider="codex",
            display_name="Codex Worker",
            owner_user_id="demo_user_alice",
            visibility_scope=AgentVisibilityScope.TEAM,
            created_at=f"{DEMO_DAY}T09:10:00+00:00",
        ),
        AgentIdentity(
            id="demo_agent_ci",
            org_id=DEMO_ORG_ID,
            provider="github-actions",
            display_name="CI Status Agent",
            owner_user_id=None,
            visibility_scope=AgentVisibilityScope.TEAM,
            created_at=f"{DEMO_DAY}T09:11:00+00:00",
        ),
    ]
    for agent in agents:
        if not store.get_agent_identity(agent.id):
            store.insert_agent_identity(agent)


def _seed_events(store: WorkEventStore) -> None:
    events = [
        WorkEvent(
            id="demo_event_private_note",
            org_id=DEMO_ORG_ID,
            team_id=DEMO_TEAM_ID,
            project_id=DEMO_PROJECT_ID,
            workspace_id=DEMO_WORKSPACE_ID,
            actor_type="user",
            actor_id="demo_user_alice",
            source_type=WorkEventSourceType.MANUAL,
            visibility=WorkEventVisibility.PRIVATE,
            retention_class=RetentionClass.SENSITIVE,
            body=DEMO_PRIVATE_NOTE_BODY,
            metadata={"kind": "private_note"},
            created_at=f"{DEMO_DAY}T10:00:00+00:00",
        ),
        WorkEvent(
            id="demo_event_team_blocker",
            org_id=DEMO_ORG_ID,
            team_id=DEMO_TEAM_ID,
            project_id=DEMO_PROJECT_ID,
            workspace_id=DEMO_WORKSPACE_ID,
            actor_type="user",
            actor_id="demo_user_bob",
            source_type=WorkEventSourceType.MANUAL,
            visibility=WorkEventVisibility.TEAM_RAW,
            retention_class=RetentionClass.STANDARD,
            body="Blocked on staging OAuth redirect mismatch for checkout demo.",
            metadata={
                "kind": "blocker",
                "blocked": "staging OAuth redirect mismatch",
                "status": "blocked",
                "task_id": "demo_task_oauth_redirect",
            },
            created_at=f"{DEMO_DAY}T10:20:00+00:00",
        ),
        WorkEvent(
            id="demo_event_completed_item",
            org_id=DEMO_ORG_ID,
            team_id=DEMO_TEAM_ID,
            project_id=DEMO_PROJECT_ID,
            workspace_id=DEMO_WORKSPACE_ID,
            actor_type="agent",
            actor_id="demo_agent_codex",
            source_type=WorkEventSourceType.AGENT_TASK_MESSAGE,
            visibility=WorkEventVisibility.TEAM_RAW,
            retention_class=RetentionClass.STANDARD,
            body="Completed permissioned retrieval demo fixtures and status QA checks.",
            metadata={
                "kind": "completed_item",
                "status": "completed",
                "summary": "Completed retrieval demo fixtures.",
                "task_id": "demo_task_retrieval_fixtures",
            },
            created_at=f"{DEMO_DAY}T10:40:00+00:00",
        ),
    ]
    for event in events:
        if not store.get_work_event(event.id):
            store.insert_work_event(event)


def _seed_result() -> dict[str, Any]:
    return {
        "org_id": DEMO_ORG_ID,
        "team_id": DEMO_TEAM_ID,
        "project_id": DEMO_PROJECT_ID,
        "workspace_id": DEMO_WORKSPACE_ID,
        "day": DEMO_DAY,
        "users": {
            "pm": "demo_user_pm",
            "manager": "demo_user_manager",
            "alice_dev": "demo_user_alice",
            "bob_dev": "demo_user_bob",
        },
        "agents": {
            "codex": "demo_agent_codex",
            "ci": "demo_agent_ci",
        },
        "events": {
            "private_note": "demo_event_private_note",
            "team_blocker": "demo_event_team_blocker",
            "completed_item": "demo_event_completed_item",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed org-collab pilot demo data.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Directory where the central org.db should be created.",
    )
    args = parser.parse_args()

    print(json.dumps(seed_org_demo_data(args.data_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
