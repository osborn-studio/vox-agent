"""Tests for organization daily and project summary generation."""

import tempfile
from pathlib import Path

import pytest

from engine.org_summaries import (
    generate_individual_daily_summary,
    generate_project_progress_report,
    generate_team_daily_summary,
)
from engine.source_adapters import manual_note_to_work_event
from engine.work_events import WorkEventStore, WorkEventVisibility


@pytest.fixture
def work_event_store():
    with tempfile.TemporaryDirectory() as td:
        store = WorkEventStore(Path(td))
        store.connect()
        yield store
        store.close()


def _insert_note(
    store,
    *,
    body,
    event_id,
    actor_id="user_1",
    team_id="team_1",
    project_id="project_1",
    created_at="2026-05-17T09:00:00+00:00",
    metadata=None,
    private=False,
):
    event = manual_note_to_work_event(
        org_id="org_1",
        actor_user_id=actor_id,
        note=body,
        team_id=team_id,
        project_id=project_id,
        private=private,
    )
    event.id = event_id
    event.created_at = created_at
    event.metadata = metadata or {}
    store.insert_work_event(event)
    return event


def test_individual_daily_summary_uses_one_users_events_for_day(work_event_store):
    _insert_note(
        work_event_store,
        body="Completed org identity fixture setup.",
        event_id="event_today_1",
        actor_id="user_1",
    )
    _insert_note(
        work_event_store,
        body="Blocked on access policy review.",
        event_id="event_today_2",
        actor_id="user_1",
        created_at="2026-05-17T15:00:00+00:00",
    )
    _insert_note(
        work_event_store,
        body="Other teammate update.",
        event_id="event_other_user",
        actor_id="user_2",
    )
    _insert_note(
        work_event_store,
        body="Yesterday update.",
        event_id="event_yesterday",
        actor_id="user_1",
        created_at="2026-05-16T15:00:00+00:00",
    )

    summary = generate_individual_daily_summary(
        work_event_store,
        org_id="org_1",
        actor_id="user_1",
        day="2026-05-17",
    )

    assert summary["visibility"] == WorkEventVisibility.PRIVATE.value
    assert summary["source_ids"] == ["event_today_1", "event_today_2"]
    assert summary["source_count"] == 2
    assert summary["confidence"] == pytest.approx(0.7)
    assert "Completed org identity fixture setup." in summary["body"]
    assert "Blocked on access policy review." in summary["body"]
    assert "Other teammate update." not in summary["body"]
    assert "Yesterday update." not in summary["body"]


def test_team_summary_excludes_private_body_and_citations(work_event_store):
    _insert_note(
        work_event_store,
        body="Shipped source adapter tests.",
        event_id="event_public",
        actor_id="user_1",
    )
    _insert_note(
        work_event_store,
        body="Sensitive private incident details.",
        event_id="event_private",
        actor_id="user_2",
        private=True,
    )

    summary = generate_team_daily_summary(
        work_event_store,
        org_id="org_1",
        team_id="team_1",
        day="2026-05-17",
    )

    assert summary["visibility"] == WorkEventVisibility.TEAM_SUMMARY.value
    assert summary["source_ids"] == ["event_public"]
    assert summary["source_count"] == 1
    assert summary["confidence"] == pytest.approx(0.6)
    assert "Shipped source adapter tests." in summary["body"]
    assert "Sensitive private incident details." not in summary["body"]


def test_project_progress_report_groups_completed_blocked_next(work_event_store):
    _insert_note(
        work_event_store,
        body="Completed event ingest storage.",
        event_id="completed_body",
        metadata={"status": "completed", "summary": "Completed policy checks."},
    )
    _insert_note(
        work_event_store,
        body="Blocked by unclear manager access rules.",
        event_id="blocked_body",
        created_at="2026-05-17T10:00:00+00:00",
    )
    _insert_note(
        work_event_store,
        body="Next wire summary endpoint.",
        event_id="next_body",
        created_at="2026-05-17T11:00:00+00:00",
        metadata={"next": "Add reporting facade."},
    )
    _insert_note(
        work_event_store,
        body="Private completed sensitive launch note.",
        event_id="private_completed",
        private=True,
    )

    report = generate_project_progress_report(
        work_event_store,
        org_id="org_1",
        project_id="project_1",
        day="2026-05-17",
    )

    assert report["visibility"] == WorkEventVisibility.PROJECT_SUMMARY.value
    assert report["source_ids"] == ["completed_body", "blocked_body", "next_body"]
    assert report["source_count"] == 3
    assert report["confidence"] == pytest.approx(0.8)
    assert report["completed"] == [
        "Completed policy checks.",
        "Completed event ingest storage.",
    ]
    assert report["blocked"] == ["Blocked by unclear manager access rules."]
    assert report["next"] == ["Add reporting facade.", "Next wire summary endpoint."]
    assert "Private completed sensitive launch note." not in report["body"]
