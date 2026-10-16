"""Tests for work event ingest and secret redaction."""

import tempfile
from pathlib import Path

import pytest

from engine.models import TaskMessage
from engine.source_adapters import (
    agent_task_message_to_work_event,
    git_event_to_work_event,
    manual_note_to_work_event,
    terminal_event_to_work_event,
    voice_transcript_to_work_event,
)
from engine.work_events import (
    RetentionClass,
    WorkEventSourceType,
    WorkEventStore,
    WorkEventVisibility,
)


@pytest.fixture
def work_event_store():
    with tempfile.TemporaryDirectory() as td:
        store = WorkEventStore(Path(td))
        store.connect()
        yield store
        store.close()


class TestWorkEventAdapters:
    def test_voice_transcript_ingests_as_work_event(self):
        event = voice_transcript_to_work_event(
            org_id="org_1",
            actor_user_id="user_1",
            transcript="We fixed the checkout retry bug.",
            team_id="team_1",
            project_id="checkout",
        )

        assert event.source_type == WorkEventSourceType.VOICE
        assert event.actor_type == "user"
        assert event.actor_id == "user_1"
        assert event.body == "We fixed the checkout retry bug."
        assert event.visibility == WorkEventVisibility.TEAM_RAW
        assert event.retention_class == RetentionClass.STANDARD

    def test_agent_task_message_ingests_as_work_event(self):
        msg = TaskMessage(
            task_id="task_1",
            seq=1,
            author_type="agent",
            author_id="codex",
            type="text",
            content="Implemented org identity tests.",
        )

        event = agent_task_message_to_work_event(
            org_id="org_1",
            task_message=msg,
            team_id="team_1",
        )

        assert event.source_type == WorkEventSourceType.AGENT_TASK_MESSAGE
        assert event.actor_type == "agent"
        assert event.actor_id == "codex"
        assert event.body == "Implemented org identity tests."
        assert event.metadata["task_id"] == "task_1"
        assert event.metadata["message_id"] == msg.id

    def test_default_workspace_event_visibility_is_team_raw(self):
        event = terminal_event_to_work_event(
            org_id="org_1",
            actor_user_id="user_1",
            command="python -m pytest tests/org_collab -q",
            output="39 passed",
            workspace_id="repo_vox_agent",
            team_id="team_1",
        )

        assert event.visibility == WorkEventVisibility.TEAM_RAW
        assert event.workspace_id == "repo_vox_agent"
        assert "39 passed" in event.body

    def test_private_span_overrides_team_raw_default(self):
        event = manual_note_to_work_event(
            org_id="org_1",
            actor_user_id="user_1",
            note="Private scratchpad about a sensitive incident.",
            private=True,
            team_id="team_1",
        )

        assert event.visibility == WorkEventVisibility.PRIVATE
        assert event.retention_class == RetentionClass.SENSITIVE

    def test_all_initial_source_types_normalize_to_work_events(self):
        events = [
            voice_transcript_to_work_event("org_1", "user_1", "voice"),
            agent_task_message_to_work_event(
                org_id="org_1",
                task_message={
                    "id": "msg_1",
                    "task_id": "task_1",
                    "author_type": "agent",
                    "author_id": "codex",
                    "content": "agent",
                },
            ),
            terminal_event_to_work_event("org_1", "user_1", "ls", "ok"),
            git_event_to_work_event("org_1", "user_1", "abc123", "fix bug"),
            manual_note_to_work_event("org_1", "user_1", "manual"),
        ]

        assert {event.source_type for event in events} == {
            WorkEventSourceType.VOICE,
            WorkEventSourceType.AGENT_TASK_MESSAGE,
            WorkEventSourceType.TERMINAL,
            WorkEventSourceType.GIT,
            WorkEventSourceType.MANUAL,
        }


class TestWorkEventStore:
    def test_secret_redaction_before_store(self, work_event_store):
        event = manual_note_to_work_event(
            org_id="org_1",
            actor_user_id="user_1",
            note="Deploy with api_key=sk-test1234567890abcdef and continue.",
        )

        work_event_store.insert_work_event(event)
        row = work_event_store.get_work_event(event.id)

        assert "sk-test1234567890abcdef" not in row["body"]
        assert "[REDACTED:secret]" in row["body"]
        assert row["source_hash"]
        assert row["redaction_reasons"] == ["secret"]

    def test_database_file_uses_central_org_schema(self, work_event_store):
        files = list(work_event_store.data_dir.glob("*.db"))
        assert {f.name for f in files} == {"org.db"}
