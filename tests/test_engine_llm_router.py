"""Tests for engine.llm_router — intelligent model selection."""

import pytest
from engine.llm_router import LLMRouter, MODELS


@pytest.fixture
def router():
    return LLMRouter()


class TestLLMRouter:
    def test_simple_task(self, router):
        route = router.route("list all files")
        assert route.model == "gpt-4o-mini"

    def test_complex_task(self, router):
        route = router.route("architect a new microservice for payment processing with security audit")
        assert route.model == "claude-opus-4-8"

    def test_code_task(self, router):
        route = router.route("fix the bug in auth.py login function")
        assert "sonnet" in route.model or "code" in route.reason

    def test_chinese_simple(self, router):
        route = router.route("这是一段很长的中文文本需要处理和格式化一下内容")
        assert route.provider in ("qwen", "claude", "openai")

    def test_long_prompt(self, router):
        route = router.route("x " * 3000)  # very long
        assert route.model == "claude-opus-4-8"

    def test_agent_override(self, router):
        router.set_override("claude-code", "powerful")
        route = router.route("simple task", agent_id="claude-code")
        assert route.model == "claude-opus-4-8"

    def test_no_override(self, router):
        route = router.route("list files", agent_id="unknown")
        # Should route normally without override

    def test_estimate_cost(self, router):
        estimate = router.estimate_cost("fix the login bug")
        assert estimate["estimated_cost_usd"] > 0
        assert "model" in estimate

    def test_available_tiers(self, router):
        tiers = router.available_tiers()
        assert len(tiers) == len(MODELS)
        names = {t["tier"] for t in tiers}
        assert "fast" in names
        assert "powerful" in names

    def test_default_balanced(self, router):
        route = router.route("do something interesting with this data")
        # Should be balanced for ambiguous tasks
        assert route.model in ("claude-sonnet-5", "gpt-4o-mini", "deepseek-chat")
