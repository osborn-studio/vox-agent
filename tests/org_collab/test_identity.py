"""Tests for organization collaboration identity models and storage."""

import tempfile
from pathlib import Path

import pytest

from engine.org_models import (
    AgentIdentity,
    AgentVisibilityScope,
    Organization,
    OrgRole,
    Project,
    ProjectMember,
    ProjectRole,
    RequestContext,
    Team,
    TeamMember,
    TeamRole,
    User,
)
from engine.org_store import OrgStore


@pytest.fixture
def org_store():
    with tempfile.TemporaryDirectory() as td:
        store = OrgStore(Path(td))
        store.connect()
        yield store
        store.close()


class TestOrgModels:
    def test_model_defaults(self):
        org = Organization(name="Vox Agent")
        user = User(
            org_id=org.id,
            email="pm@example.com",
            display_name="Product Manager",
        )
        agent = AgentIdentity(
            org_id=org.id,
            provider="codex",
            display_name="Codex",
        )
        ctx = RequestContext(
            org_id=org.id,
            requester_user_id=user.id,
            purpose="daily status query",
        )

        assert len(org.id) == 12
        assert user.role == OrgRole.MEMBER
        assert agent.visibility_scope == AgentVisibilityScope.TEAM
        assert ctx.team_id is None
        assert "T" in org.created_at


class TestOrgStore:
    def test_create_and_read_org_user_team_membership(self, org_store):
        org = Organization(name="Vox Agent")
        user = User(
            org_id=org.id,
            email="dev@example.com",
            display_name="Developer",
            role=OrgRole.PRODUCT_MANAGER,
        )
        team = Team(org_id=org.id, name="Coding")
        member = TeamMember(
            team_id=team.id,
            user_id=user.id,
            role=TeamRole.LEAD,
        )

        org_store.insert_organization(org)
        org_store.insert_user(user)
        org_store.insert_team(team)
        org_store.insert_team_member(member)

        assert org_store.get_organization(org.id)["name"] == "Vox Agent"
        assert org_store.get_user(user.id)["email"] == "dev@example.com"
        assert org_store.get_user_by_email(org.id, "dev@example.com")["id"] == user.id
        assert org_store.get_team(team.id)["name"] == "Coding"

        members = org_store.list_team_members(team.id)
        assert len(members) == 1
        assert members[0]["user_id"] == user.id
        assert members[0]["role"] == "lead"

    def test_team_role_wins_when_resolving_role_for_team_context(self, org_store):
        org = Organization(name="Vox Agent")
        user = User(
            org_id=org.id,
            email="eng@example.com",
            display_name="Engineer",
            role=OrgRole.ADMIN,
        )
        team = Team(org_id=org.id, name="Coding")
        member = TeamMember(
            team_id=team.id,
            user_id=user.id,
            role=TeamRole.MEMBER,
        )

        org_store.insert_organization(org)
        org_store.insert_user(user)
        org_store.insert_team(team)
        org_store.insert_team_member(member)

        assert org_store.resolve_user_role(user.id) == "admin"
        assert org_store.resolve_user_role(user.id, team_id=team.id) == "member"

    def test_project_membership_is_explicit_and_org_scoped(self, org_store):
        org = Organization(name="Vox Agent")
        user = User(
            org_id=org.id,
            email="project@example.com",
            display_name="Project Member",
        )
        team = Team(org_id=org.id, name="Platform")
        project = Project(org_id=org.id, team_id=team.id, name="Vox Agent 2")
        member = ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role=ProjectRole.LEAD,
        )

        org_store.insert_organization(org)
        org_store.insert_user(user)
        org_store.insert_team(team)
        org_store.insert_project(project)
        org_store.insert_project_member(member)

        assert org_store.get_project(project.id)["org_id"] == org.id
        assert org_store.get_project(project.id)["team_id"] == team.id
        assert org_store.get_project_role(user.id, project.id) == "lead"

    def test_agent_identity_is_scoped_to_organization(self, org_store):
        org = Organization(name="Vox Agent")
        agent = AgentIdentity(
            org_id=org.id,
            provider="github-copilot",
            display_name="GitHub Copilot",
            visibility_scope=AgentVisibilityScope.ORG,
        )

        org_store.insert_organization(org)
        org_store.insert_agent_identity(agent)

        row = org_store.get_agent_identity(agent.id)
        assert row["org_id"] == org.id
        assert row["provider"] == "github-copilot"
        assert row["visibility_scope"] == "org"
        assert org_store.list_agents(org.id)[0]["id"] == agent.id

    def test_database_file_is_isolated(self, org_store):
        files = list(org_store.data_dir.glob("*.db"))
        assert {f.name for f in files} == {"org.db"}
