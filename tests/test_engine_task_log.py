"""Tests for engine.task_log — structured execution logging."""

import pytest
from engine.task_log import TaskLog


@pytest.fixture
def tlog(tmp_path):
    return TaskLog(tmp_path / "logs")


class TestTaskLog:
    def test_log_and_read(self, tlog):
        tlog.log("t1", "intent", text="hello", intent="task")
        entries = tlog.get_log("t1")
        assert len(entries) == 1
        assert entries[0].step == "intent"
        assert entries[0].data["intent"] == "task"

    def test_multiple_entries(self, tlog):
        tlog.log_intent("t1", "帮我跑测试", "task", 0.85)
        tlog.log_dispatch("t1", "claude-code", "claude")
        tlog.log_tool_call("t1", "bash", {"command": "pytest"})
        tlog.log_tool_result("t1", "bash", "5 passed", True)
        tlog.log_status("t1", "running", "completed")

        entries = tlog.get_log("t1")
        assert len(entries) == 5
        steps = [e.step for e in entries]
        assert steps == ["intent", "dispatch", "tool_call", "tool_result", "status"]

    def test_error_log(self, tlog):
        tlog.log_error("t1", "LLM API timeout", recoverable=True)
        errors = tlog.get_errors("t1")
        assert len(errors) == 1
        assert errors[0].data["recoverable"] is True

    def test_llm_call_log(self, tlog):
        tlog.log_llm_call("t1", "claude-opus-4-6", 1000, 500, 0.05)
        entries = tlog.get_log("t1")
        assert entries[0].data["cost"] == 0.05

    def test_timeline(self, tlog):
        tlog.log_intent("t1", "fix bug", "task", 0.9)
        tlog.log_dispatch("t1", "claude", "claude")
        tlog.log_tool_call("t1", "read", {"file_path": "auth.py"})
        tlog.log_status("t1", "running", "completed")

        timeline = tlog.get_timeline("t1")
        assert len(timeline) == 4
        assert "task" in timeline[0]["detail"]
        assert "claude" in timeline[1]["detail"]

    def test_empty_log(self, tlog):
        assert tlog.get_log("nonexistent") == []
        assert tlog.get_errors("nonexistent") == []
        assert tlog.get_timeline("nonexistent") == []

    def test_separate_task_logs(self, tlog):
        tlog.log("t1", "intent", text="task 1")
        tlog.log("t2", "intent", text="task 2")
        assert len(tlog.get_log("t1")) == 1
        assert len(tlog.get_log("t2")) == 1

    def test_cleanup(self, tlog):
        tlog.log("t1", "test")
        # Make file old
        import os
        import time
        path = tlog._path("t1")
        old_time = time.time() - (31 * 86400)
        os.utime(path, (old_time, old_time))

        removed = tlog.cleanup(max_age_days=30)
        assert removed == 1
        assert tlog.get_log("t1") == []

    def test_truncation(self, tlog):
        long_text = "x" * 10000
        tlog.log_tool_call("t1", "read", {"content": long_text})
        entries = tlog.get_log("t1")
        assert len(entries[0].data["input"]["content"]) < 500
