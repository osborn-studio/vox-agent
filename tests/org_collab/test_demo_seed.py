"""Tests for repeatable organization collaboration demo seed data."""

from engine.access_audit import AccessAuditStore
from engine.org_demo_seed import DEMO_PRIVATE_NOTE_BODY, seed_org_demo_data
from engine.org_models import RequestContext
from engine.org_store import OrgStore
from engine.org_summaries import generate_team_daily_summary
from engine.permissioned_retrieval import PermissionedRetriever
from engine.policy import PolicyEngine
from engine.work_events import WorkEventStore, WorkEventVisibility


def test_demo_seed_creates_org_people_agents_and_work_events(tmp_path):
    result = seed_org_demo_data(tmp_path)

    org_store = OrgStore(tmp_path)
    event_store = WorkEventStore(tmp_path)
    org_store.connect()
    event_store.connect()

    try:
        assert org_store.get_organization(result["org_id"])["name"] == "Vox Agent"
        members = org_store.list_team_members(result["team_id"])
        assert len(members) == 4
        assert {
            org_store.get_user(member["user_id"])["email"] for member in members
        } == {
            "pm@voxagent.local",
            "manager@voxagent.local",
            "alice.dev@voxagent.local",
            "bob.dev@voxagent.local",
        }
        assert len(org_store.list_agents(result["org_id"])) == 2

        events = event_store.list_work_events(result["org_id"], result["team_id"])
        assert len(events) == 3
        assert any(event["body"] == DEMO_PRIVATE_NOTE_BODY for event in events)
        assert any(event["metadata"].get("blocked") for event in events)
        assert any(event["metadata"].get("status") == "completed" for event in events)
        assert {
            event["visibility"] for event in events if event["body"] == DEMO_PRIVATE_NOTE_BODY
        } == {WorkEventVisibility.PRIVATE.value}
    finally:
        event_store.close()
        org_store.close()


def test_demo_seed_is_idempotent_within_one_store(tmp_path):
    first = seed_org_demo_data(tmp_path)
    second = seed_org_demo_data(tmp_path)

    org_store = OrgStore(tmp_path)
    event_store = WorkEventStore(tmp_path)
    org_store.connect()
    event_store.connect()

    try:
        assert first == second
        assert len(org_store.list_team_members(first["team_id"])) == 4
        assert len(org_store.list_agents(first["org_id"])) == 2
        assert len(event_store.list_work_events(first["org_id"], first["team_id"])) == 3
    finally:
        event_store.close()
        org_store.close()


def test_demo_private_note_is_stored_but_not_leaked_to_team_summary_or_query(tmp_path):
    result = seed_org_demo_data(tmp_path)

    org_store = OrgStore(tmp_path)
    event_store = WorkEventStore(tmp_path)
    audit_store = AccessAuditStore(tmp_path)
    org_store.connect()
    event_store.connect()
    audit_store.connect()

    try:
        private_event = event_store.get_work_event(result["events"]["private_note"])
        assert private_event["body"] == DEMO_PRIVATE_NOTE_BODY

        team_summary = generate_team_daily_summary(
            event_store,
            org_id=result["org_id"],
            team_id=result["team_id"],
            day=result["day"],
        )
        assert DEMO_PRIVATE_NOTE_BODY not in team_summary["body"]
        assert result["events"]["private_note"] not in team_summary["source_ids"]

        retriever = PermissionedRetriever(
            event_store=event_store,
            policy=PolicyEngine(org_store=org_store, audit_store=audit_store),
        )
        bob_ctx = RequestContext(
            org_id=result["org_id"],
            requester_user_id=result["users"]["bob_dev"],
            team_id=result["team_id"],
            purpose="team demo status",
        )

        results = retriever.search(bob_ctx, "private roadmap")

        assert results == []
    finally:
        audit_store.close()
        event_store.close()
        org_store.close()
