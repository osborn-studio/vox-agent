"""Tests for engine.agent_router — automatic agent selection."""

import pytest
from engine.agent_router import AgentRouter
from engine.agent_config import AgentConfigScanner


@pytest.fixture
def router(tmp_path):
    scanner = AgentConfigScanner(tmp_path / "agents")
    return AgentRouter(scanner)


class TestAgentRouter:
    def test_user_specified(self, router):
        result = router.route("anything", preferred_agent="my-agent")
        assert result.agent_id == "my-agent"
        assert result.confidence == 1.0

    def test_code_keywords(self, router):
        result = router.route("fix the login bug in auth.py")
        assert result.agent_id == "claude-code"
        assert result.confidence > 0.5

    def test_chinese_code(self, router):
        result = router.route("帮我修一下代码")
        assert result.agent_id == "claude-code"

    def test_file_extension(self, router):
        result = router.route("review changes in main.swift")
        assert result.agent_id == "claude-code"

    def test_openclaw(self, router):
        result = router.route("用小龙虾执行")
        assert result.agent_id == "openclaw"

    def test_note_taking(self, router):
        result = router.route("记一下明天开会")
        assert result.agent_id == "general"

    def test_default_fallback(self, router):
        result = router.route("今天天气怎么样")
        assert result.agent_id == "claude-code"
        assert result.reason == "default (no keyword match)"

    def test_suggest_agents(self, router):
        suggestions = router.suggest_agents("fix bug in auth.py and deploy")
        assert len(suggestions) > 0
        ids = {s.agent_id for s in suggestions}
        assert "claude-code" in ids

    def test_suggest_empty(self, router):
        suggestions = router.suggest_agents("随便说点什么")
        # May or may not have suggestions depending on keyword matching

    def test_multiple_matches(self, router):
        result = router.route("write code and deploy to staging")
        assert result.confidence >= 0.3
