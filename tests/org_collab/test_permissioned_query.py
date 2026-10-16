"""Tests for permission decisions and raw work-context access audit."""

import tempfile
from pathlib import Path

import pytest

from engine.access_audit import AccessAuditStore
from engine.org_models import Organization, RequestContext, Team, TeamMember, User
from engine.org_store import OrgStore
from engine.policy import PolicyEngine
from engine.source_adapters import manual_note_to_work_event
from engine.work_events import WorkEventVisibility


@pytest.fixture
def policy_components():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        org_store = OrgStore(data_dir)
        audit_store = AccessAuditStore(data_dir)
        org_store.connect()
        audit_store.connect()

        org = Organization(name="Vox Agent")
        alice = User(org_id=org.id, email="alice@example.com", display_name="Alice")
        bob = User(org_id=org.id, email="bob@example.com", display_name="Bob")
        team = Team(org_id=org.id, name="Coding")

        org_store.insert_organization(org)
        org_store.insert_user(alice)
        org_store.insert_user(bob)
        org_store.insert_team(team)
        org_store.insert_team_member(TeamMember(team_id=team.id, user_id=alice.id))
        org_store.insert_team_member(TeamMember(team_id=team.id, user_id=bob.id))

        policy = PolicyEngine(org_store=org_store, audit_store=audit_store)
        yield policy, audit_store, org, team, alice, bob

        audit_store.close()
        org_store.close()


class TestPolicyEngine:
    def test_creator_can_read_private_event(self, policy_components):
        policy, audit_store, org, team, alice, _bob = policy_components
        event = manual_note_to_work_event(
            org_id=org.id,
            actor_user_id=alice.id,
            note="private note",
            team_id=team.id,
            private=True,
        )
        ctx = RequestContext(
            org_id=org.id,
            requester_user_id=alice.id,
            team_id=team.id,
            purpose="review my own context",
        )

        decision = policy.can_read_event(ctx, event)

        assert decision.allowed
        assert decision.reason == "creator_private_access"
        assert audit_store.list_audit_records(org.id) == []

    def test_teammate_cannot_read_private_event(self, policy_components):
        policy, audit_store, org, team, alice, bob = policy_components
        event = manual_note_to_work_event(
            org_id=org.id,
            actor_user_id=alice.id,
            note="private note",
            team_id=team.id,
            private=True,
        )
        ctx = RequestContext(
            org_id=org.id,
            requester_user_id=bob.id,
            team_id=team.id,
            purpose="check teammate details",
        )

        decision = policy.can_read_event(ctx, event)

        assert not decision.allowed
        assert decision.reason == "private_event_owner_only"
        audits = audit_store.list_audit_records(org.id)
        assert len(audits) == 1
        assert audits[0]["decision"] == "denied"
        assert audits[0]["source_ids"] == [event.id]

    def test_manager_can_read_team_raw_work_context_by_default(self, policy_components):
        policy, audit_store, org, team, alice, bob = policy_components
        event = manual_note_to_work_event(
            org_id=org.id,
            actor_user_id=alice.id,
            note="work update",
            team_id=team.id,
        )
        ctx = RequestContext(
            org_id=org.id,
            requester_user_id=bob.id,
            team_id=team.id,
            purpose="daily status",
        )

        decision = policy.can_read_event(ctx, event)

        assert decision.allowed
        assert decision.reason == "team_raw_member_access"
        audits = audit_store.list_audit_records(org.id)
        assert len(audits) == 1
        assert audits[0]["decision"] == "allowed"

    def test_non_member_cannot_read_team_raw_event(self, policy_components):
        policy, audit_store, org, team, alice, _bob = policy_components
        outsider = User(
            org_id=org.id,
            email="outsider@example.com",
            display_name="Outsider",
        )
        policy.org_store.insert_user(outsider)
        event = manual_note_to_work_event(
            org_id=org.id,
            actor_user_id=alice.id,
            note="team context",
            team_id=team.id,
        )
        ctx = RequestContext(
            org_id=org.id,
            requester_user_id=outsider.id,
            team_id=team.id,
            purpose="status",
        )

        decision = policy.can_read_event(ctx, event)

        assert not decision.allowed
        assert decision.reason == "team_membership_required"
        assert audit_store.list_audit_records(org.id)[0]["decision"] == "denied"

    def test_org_mismatch_denies_without_leaking_scope(self, policy_components):
        policy, audit_store, org, team, alice, bob = policy_components
        event = manual_note_to_work_event(
            org_id="other_org",
            actor_user_id=alice.id,
            note="other org context",
            team_id=team.id,
        )
        ctx = RequestContext(
            org_id=org.id,
            requester_user_id=bob.id,
            team_id=team.id,
            purpose="status",
        )

        decision = policy.can_read_event(ctx, event)

        assert not decision.allowed
        assert decision.reason == "org_mismatch"
        assert audit_store.list_audit_records(org.id)[0]["decision"] == "denied"

    def test_public_summary_visibility_is_allowed_in_org(self, policy_components):
        policy, _audit_store, org, team, alice, bob = policy_components
        event = manual_note_to_work_event(
            org_id=org.id,
            actor_user_id=alice.id,
            note="summary",
            team_id=team.id,
        )
        event.visibility = WorkEventVisibility.ORG_SUMMARY
        ctx = RequestContext(
            org_id=org.id,
            requester_user_id=bob.id,
            team_id=team.id,
            purpose="summary",
        )

        decision = policy.can_read_event(ctx, event)

        assert decision.allowed
        assert decision.reason == "org_summary_access"
