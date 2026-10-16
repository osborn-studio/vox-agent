"""Tests for engine.models — Task state machine and data structures."""

from engine.models import (
    Task, TaskStatus, TaskPriority, EngineEvent, EventType, TaskMessage, ActivityEntry,
)


class TestTaskStateMachine:
    def test_initial_state_is_draft(self):
        t = Task(agent_id="claude", title="test")
        assert t.status == TaskStatus.DRAFT

    def test_draft_can_transition_to_planned(self):
        t = Task(agent_id="claude", title="test")
        assert t.can_transition(TaskStatus.PLANNED)

    def test_draft_can_transition_to_cancelled(self):
        t = Task(agent_id="claude", title="test")
        assert t.can_transition(TaskStatus.CANCELLED)

    def test_draft_cannot_skip_to_running(self):
        t = Task(agent_id="claude", title="test")
        assert not t.can_transition(TaskStatus.RUNNING)

    def test_draft_cannot_skip_to_completed(self):
        t = Task(agent_id="claude", title="test")
        assert not t.can_transition(TaskStatus.COMPLETED)

    def test_running_can_complete(self):
        t = Task(agent_id="claude", title="test", status=TaskStatus.RUNNING)
        assert t.can_transition(TaskStatus.COMPLETED)

    def test_running_can_fail(self):
        t = Task(agent_id="claude", title="test", status=TaskStatus.RUNNING)
        assert t.can_transition(TaskStatus.FAILED)

    def test_running_can_block(self):
        t = Task(agent_id="claude", title="test", status=TaskStatus.RUNNING)
        assert t.can_transition(TaskStatus.BLOCKED)

    def test_blocked_can_resume(self):
        t = Task(agent_id="claude", title="test", status=TaskStatus.BLOCKED)
        assert t.can_transition(TaskStatus.RUNNING)

    def test_completed_is_terminal(self):
        t = Task(agent_id="claude", title="test", status=TaskStatus.COMPLETED)
        assert not t.can_transition(TaskStatus.RUNNING)
        assert not t.can_transition(TaskStatus.FAILED)

    def test_every_state_can_cancel(self):
        for status in [TaskStatus.DRAFT, TaskStatus.PLANNED, TaskStatus.ASSIGNED,
                       TaskStatus.RUNNING, TaskStatus.BLOCKED]:
            t = Task(agent_id="claude", title="test", status=status)
            assert t.can_transition(TaskStatus.CANCELLED), f"{status} should allow cancel"

    def test_full_happy_path(self):
        transitions = [
            TaskStatus.PLANNED, TaskStatus.ASSIGNED,
            TaskStatus.RUNNING, TaskStatus.COMPLETED,
        ]
        t = Task(agent_id="claude", title="test")
        for target in transitions:
            assert t.can_transition(target), f"should transition to {target}"
            t.status = target


class TestTaskDefaults:
    def test_auto_id(self):
        t = Task(agent_id="claude", title="test")
        assert len(t.id) == 12

    def test_unique_ids(self):
        ids = {Task(agent_id="claude", title="test").id for _ in range(100)}
        assert len(ids) == 100

    def test_default_priority(self):
        t = Task(agent_id="claude", title="test")
        assert t.priority == TaskPriority.MEDIUM

    def test_created_at_set(self):
        t = Task(agent_id="claude", title="test")
        assert t.created_at is not None
        assert "T" in t.created_at  # ISO format


class TestEngineEvent:
    def test_text_delta(self):
        e = EngineEvent(type=EventType.TEXT_DELTA, data="hello")
        assert e.type == EventType.TEXT_DELTA
        assert e.data == "hello"

    def test_tool_use(self):
        e = EngineEvent(type=EventType.TOOL_USE, tool="bash", input={"command": "ls"})
        assert e.tool == "bash"
        assert e.input == {"command": "ls"}


class TestTaskMessage:
    def test_auto_id(self):
        m = TaskMessage(task_id="t1", seq=1, author_type="agent",
                        author_id="claude", type="text", content="hi")
        assert len(m.id) == 12

    def test_fields(self):
        m = TaskMessage(task_id="t1", seq=3, author_type="agent",
                        author_id="claude", type="tool_use",
                        tool="bash", input={"command": "pwd"})
        assert m.seq == 3
        assert m.tool == "bash"


class TestActivityEntry:
    def test_defaults(self):
        a = ActivityEntry(actor_type="system", actor_id="scheduler", action="task:created")
        assert len(a.id) == 12
        assert a.target_type == ""
