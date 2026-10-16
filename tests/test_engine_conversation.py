"""Tests for engine.conversation — multi-turn conversation tracking."""

import pytest
from engine.conversation import ConversationManager


@pytest.fixture
def mgr():
    return ConversationManager(max_messages_per_agent=10)


class TestConversationManager:
    def test_add_and_get(self, mgr):
        mgr.add_user_message("claude", "hello")
        mgr.add_agent_message("claude", "hi there")
        history = mgr.get_history("claude")
        assert len(history) == 2
        assert history[0].role == "user"
        assert history[1].role == "agent"

    def test_max_messages(self, mgr):
        for i in range(20):
            mgr.add_user_message("claude", f"msg {i}")
        assert mgr.message_count("claude") == 10  # max is 10

    def test_separate_agents(self, mgr):
        mgr.add_user_message("claude", "for claude")
        mgr.add_user_message("codex", "for codex")
        assert mgr.message_count("claude") == 1
        assert mgr.message_count("codex") == 1

    def test_get_with_limit(self, mgr):
        for i in range(5):
            mgr.add_user_message("claude", f"msg {i}")
        history = mgr.get_history("claude", limit=2)
        assert len(history) == 2
        assert history[0].content == "msg 3"

    def test_context_messages(self, mgr):
        mgr.add_user_message("claude", "what is 2+2?")
        mgr.add_agent_message("claude", "4")
        ctx = mgr.get_context_messages("claude")
        assert ctx[0]["role"] == "user"
        assert ctx[1]["role"] == "assistant"  # agent → assistant for LLM

    def test_system_message(self, mgr):
        mgr.add_system_message("claude", "task completed")
        history = mgr.get_history("claude")
        assert history[0].role == "system"

    def test_clear(self, mgr):
        mgr.add_user_message("claude", "hello")
        mgr.clear("claude")
        assert mgr.message_count("claude") == 0

    def test_clear_all(self, mgr):
        mgr.add_user_message("claude", "a")
        mgr.add_user_message("codex", "b")
        mgr.clear_all()
        assert mgr.active_conversations() == []

    def test_active_conversations(self, mgr):
        mgr.add_user_message("claude", "hi")
        mgr.add_user_message("codex", "hi")
        assert set(mgr.active_conversations()) == {"claude", "codex"}

    def test_empty_agent(self, mgr):
        assert mgr.get_history("unknown") == []
        assert mgr.message_count("unknown") == 0

    def test_task_id_tracked(self, mgr):
        mgr.add_user_message("claude", "test", task_id="t123")
        history = mgr.get_history("claude")
        assert history[0].task_id == "t123"
