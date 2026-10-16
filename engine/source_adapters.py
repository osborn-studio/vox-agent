"""Adapters that normalize capture sources into WorkEvent records."""

from __future__ import annotations

from typing import Any

from .models import TaskMessage
from .work_events import (
    RetentionClass,
    WorkEvent,
    WorkEventSourceType,
    WorkEventVisibility,
)


def _default_visibility(private: bool) -> WorkEventVisibility:
    return WorkEventVisibility.PRIVATE if private else WorkEventVisibility.TEAM_RAW


def _default_retention(private: bool) -> RetentionClass:
    return RetentionClass.SENSITIVE if private else RetentionClass.STANDARD


def voice_transcript_to_work_event(
    org_id: str,
    actor_user_id: str,
    transcript: str,
    team_id: str | None = None,
    project_id: str | None = None,
    workspace_id: str | None = None,
    private: bool = False,
) -> WorkEvent:
    return WorkEvent(
        org_id=org_id,
        team_id=team_id,
        project_id=project_id,
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=actor_user_id,
        source_type=WorkEventSourceType.VOICE,
        visibility=_default_visibility(private),
        retention_class=_default_retention(private),
        body=transcript,
    )


def agent_task_message_to_work_event(
    org_id: str,
    task_message: TaskMessage | dict[str, Any],
    team_id: str | None = None,
    project_id: str | None = None,
    workspace_id: str | None = None,
    private: bool = False,
) -> WorkEvent:
    if isinstance(task_message, TaskMessage):
        message_id = task_message.id
        task_id = task_message.task_id
        author_type = task_message.author_type
        author_id = task_message.author_id
        body = task_message.content
    else:
        message_id = task_message["id"]
        task_id = task_message["task_id"]
        author_type = task_message["author_type"]
        author_id = task_message["author_id"]
        body = task_message.get("content") or task_message.get("output") or ""

    return WorkEvent(
        org_id=org_id,
        team_id=team_id,
        project_id=project_id,
        workspace_id=workspace_id,
        actor_type=author_type,
        actor_id=author_id,
        source_type=WorkEventSourceType.AGENT_TASK_MESSAGE,
        visibility=_default_visibility(private),
        retention_class=_default_retention(private),
        body=body,
        metadata={"task_id": task_id, "message_id": message_id},
    )


def terminal_event_to_work_event(
    org_id: str,
    actor_user_id: str,
    command: str,
    output: str,
    workspace_id: str | None = None,
    team_id: str | None = None,
    project_id: str | None = None,
    private: bool = False,
) -> WorkEvent:
    body = f"$ {command}\n{output}".strip()
    return WorkEvent(
        org_id=org_id,
        team_id=team_id,
        project_id=project_id,
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=actor_user_id,
        source_type=WorkEventSourceType.TERMINAL,
        visibility=_default_visibility(private),
        retention_class=_default_retention(private),
        body=body,
        metadata={"command": command},
    )


def git_event_to_work_event(
    org_id: str,
    actor_user_id: str,
    commit_sha: str,
    message: str,
    repo_url: str | None = None,
    branch: str | None = None,
    workspace_id: str | None = None,
    team_id: str | None = None,
    project_id: str | None = None,
    private: bool = False,
) -> WorkEvent:
    return WorkEvent(
        org_id=org_id,
        team_id=team_id,
        project_id=project_id,
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=actor_user_id,
        source_type=WorkEventSourceType.GIT,
        visibility=_default_visibility(private),
        retention_class=_default_retention(private),
        body=message,
        metadata={
            "commit_sha": commit_sha,
            "repo_url": repo_url,
            "branch": branch,
        },
    )


def manual_note_to_work_event(
    org_id: str,
    actor_user_id: str,
    note: str,
    team_id: str | None = None,
    project_id: str | None = None,
    workspace_id: str | None = None,
    private: bool = False,
) -> WorkEvent:
    return WorkEvent(
        org_id=org_id,
        team_id=team_id,
        project_id=project_id,
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=actor_user_id,
        source_type=WorkEventSourceType.MANUAL,
        visibility=_default_visibility(private),
        retention_class=_default_retention(private),
        body=note,
    )
