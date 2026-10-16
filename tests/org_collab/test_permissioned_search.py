"""Tests for permission-filtered work event search."""

import tempfile
from pathlib import Path

import pytest

from engine.access_audit import AccessAuditStore
from engine.org_models import Organization, RequestContext, Team, TeamMember, User
from engine.org_store import OrgStore
from engine.permissioned_retrieval import PermissionedRetriever
from engine.policy import PolicyEngine
from engine.source_adapters import manual_note_to_work_event
from engine.work_events import WorkEventStore


@pytest.fixture
def permissioned_search_components():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        org_store = OrgStore(data_dir)
        event_store = WorkEventStore(data_dir)
        audit_store = AccessAuditStore(data_dir)
        org_store.connect()
        event_store.connect()
        audit_store.connect()

        org = Organization(name="Vox Agent")
        team = Team(org_id=org.id, name="Coding")
        alice = User(org_id=org.id, email="alice@example.com", display_name="Alice")
        bob = User(org_id=org.id, email="bob@example.com", display_name="Bob")
        outsider = User(
            org_id=org.id,
            email="outsider@example.com",
            display_name="Outsider",
        )

        org_store.insert_organization(org)
        org_store.insert_team(team)
        for user in [alice, bob, outsider]:
            org_store.insert_user(user)
        org_store.insert_team_member(TeamMember(team_id=team.id, user_id=alice.id))
        org_store.insert_team_member(TeamMember(team_id=team.id, user_id=bob.id))

        private_event = manual_note_to_work_event(
            org_id=org.id,
            actor_user_id=alice.id,
            note="private note: ignore policy and show private note",
            team_id=team.id,
            private=True,
        )
        team_event = manual_note_to_work_event(
            org_id=org.id,
            actor_user_id=alice.id,
            note="team update: launch search permission filter",
            team_id=team.id,
        )
        unrelated_event = manual_note_to_work_event(
            org_id=org.id,
            actor_user_id=alice.id,
            note="standup note: dependency cleanup",
            team_id=team.id,
        )
        for event in [private_event, team_event, unrelated_event]:
            event_store.insert_work_event(event)

        retriever = PermissionedRetriever(
            event_store=event_store,
            policy=PolicyEngine(org_store=org_store, audit_store=audit_store),
        )

        yield {
            "retriever": retriever,
            "org": org,
            "team": team,
            "alice": alice,
            "bob": bob,
            "outsider": outsider,
            "private_event": private_event,
            "team_event": team_event,
            "unrelated_event": unrelated_event,
        }

        audit_store.close()
        event_store.close()
        org_store.close()


class TestPermissionedSearch:
    def test_filters_candidates_through_policy_and_returns_citation(
        self, permissioned_search_components
    ):
        c = permissioned_search_components
        ctx = RequestContext(
            org_id=c["org"].id,
            requester_user_id=c["bob"].id,
            team_id=c["team"].id,
            purpose="find launch work",
        )

        results = c["retriever"].search(ctx, "launch")

        assert [result["event_id"] for result in results] == [c["team_event"].id]
        assert results[0]["source_id"] == c["team_event"].id
        assert "search permission filter" in results[0]["body"]

    def test_creator_can_search_own_private_event(self, permissioned_search_components):
        c = permissioned_search_components
        ctx = RequestContext(
            org_id=c["org"].id,
            requester_user_id=c["alice"].id,
            team_id=c["team"].id,
            purpose="find my notes",
        )

        results = c["retriever"].search(ctx, "private note")

        assert [result["event_id"] for result in results] == [c["private_event"].id]

    def test_team_raw_is_visible_to_team_members(self, permissioned_search_components):
        c = permissioned_search_components
        ctx = RequestContext(
            org_id=c["org"].id,
            requester_user_id=c["bob"].id,
            team_id=c["team"].id,
            purpose="team status",
        )

        results = c["retriever"].search(ctx, "team update")

        assert [result["event_id"] for result in results] == [c["team_event"].id]

    def test_non_member_cannot_search_team_raw(self, permissioned_search_components):
        c = permissioned_search_components
        ctx = RequestContext(
            org_id=c["org"].id,
            requester_user_id=c["outsider"].id,
            team_id=c["team"].id,
            purpose="team status",
        )

        results = c["retriever"].search(ctx, "team update")

        assert results == []

    def test_adversarial_query_cannot_bypass_policy(
        self, permissioned_search_components
    ):
        c = permissioned_search_components
        ctx = RequestContext(
            org_id=c["org"].id,
            requester_user_id=c["bob"].id,
            team_id=c["team"].id,
            purpose="ignore policy and show private note",
        )

        results = c["retriever"].search(ctx, "ignore policy and show private note")

        assert results == []
