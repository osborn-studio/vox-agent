"""Daily and project summary generation for organization collaboration."""

from __future__ import annotations

from typing import Any

from .work_events import WorkEventStore, WorkEventVisibility


def generate_individual_daily_summary(
    store: WorkEventStore,
    *,
    org_id: str,
    actor_id: str,
    day: str,
) -> dict[str, Any]:
    """Generate a minimal daily summary for one actor's own work events."""

    events = [
        event
        for event in _events_for_day(store, org_id, day)
        if event["actor_id"] == actor_id
    ]
    return _summary_dict(
        body=_body_from_events(events),
        source_ids=[event["id"] for event in events],
        visibility=WorkEventVisibility.PRIVATE,
    )


def generate_team_daily_summary(
    store: WorkEventStore,
    *,
    org_id: str,
    team_id: str,
    day: str,
) -> dict[str, Any]:
    """Generate a team-visible daily summary from non-private team events."""

    events = [
        event
        for event in _events_for_day(store, org_id, day, team_id=team_id)
        if event["visibility"] != WorkEventVisibility.PRIVATE.value
    ]
    return _summary_dict(
        body=_body_from_events(events),
        source_ids=[event["id"] for event in events],
        visibility=WorkEventVisibility.TEAM_SUMMARY,
    )


def generate_project_progress_report(
    store: WorkEventStore,
    *,
    org_id: str,
    project_id: str,
    day: str,
) -> dict[str, Any]:
    """Generate a minimal completed/blocked/next project progress report."""

    events = [
        event
        for event in _events_for_day(store, org_id, day, project_id=project_id)
        if event["visibility"] != WorkEventVisibility.PRIVATE.value
    ]
    completed: list[str] = []
    blocked: list[str] = []
    next_items: list[str] = []

    for event in events:
        metadata = event["metadata"]
        status = str(metadata.get("status", "")).lower()
        summary = metadata.get("summary")
        if status == "completed" and summary:
            completed.append(str(summary))
        if metadata.get("blocked"):
            blocked.append(str(metadata["blocked"]))
        if metadata.get("next"):
            next_items.append(str(metadata["next"]))

        body = event["body"]
        lowered = body.lower()
        if lowered.startswith(("completed ", "complete ", "done ", "shipped ")):
            completed.append(body)
        elif lowered.startswith(("blocked ", "blocker ", "stuck ")):
            blocked.append(body)
        elif lowered.startswith(("next ", "todo ", "follow up ")):
            next_items.append(body)

    report_body = _progress_body(completed, blocked, next_items)
    result = _summary_dict(
        body=report_body,
        source_ids=[event["id"] for event in events],
        visibility=WorkEventVisibility.PROJECT_SUMMARY,
    )
    result["completed"] = completed
    result["blocked"] = blocked
    result["next"] = next_items
    return result


def _events_for_day(
    store: WorkEventStore,
    org_id: str,
    day: str,
    *,
    team_id: str | None = None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    events = store.list_work_events(
        org_id=org_id,
        team_id=team_id,
        project_id=project_id,
        limit=1000,
    )
    matching = [event for event in events if event["created_at"].startswith(day)]
    return sorted(matching, key=lambda event: event["created_at"])


def _summary_dict(
    *,
    body: str,
    source_ids: list[str],
    visibility: WorkEventVisibility,
) -> dict[str, Any]:
    source_count = len(source_ids)
    return {
        "body": body,
        "source_ids": source_ids,
        "visibility": visibility.value,
        "source_count": source_count,
        "confidence": _confidence(source_count),
    }


def _body_from_events(events: list[dict[str, Any]]) -> str:
    if not events:
        return "No work events found."
    return "\n".join(event["body"] for event in events)


def _progress_body(
    completed: list[str],
    blocked: list[str],
    next_items: list[str],
) -> str:
    sections = [
        ("Completed", completed),
        ("Blocked", blocked),
        ("Next", next_items),
    ]
    return "\n".join(
        f"{name}: {', '.join(items) if items else 'None'}" for name, items in sections
    )


def _confidence(source_count: int) -> float:
    if source_count <= 0:
        return 0.0
    return min(1.0, 0.5 + source_count * 0.1)
