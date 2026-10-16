"""Tests for engine.token_counter — context window management."""

from engine.token_counter import (
    count_tokens, count_message_tokens, fits_context,
    truncate_to_tokens, context_budget,
)


class TestTokenCounter:
    def test_empty(self):
        assert count_tokens("") == 0

    def test_english(self):
        tokens = count_tokens("Hello, world!")
        assert 2 <= tokens <= 5

    def test_chinese(self):
        tokens = count_tokens("你好世界")
        assert tokens >= 2

    def test_mixed(self):
        tokens = count_tokens("Hello 你好 world 世界")
        assert tokens > 3

    def test_code(self):
        code = "def hello():\n    return 'world'\n"
        tokens = count_tokens(code)
        assert tokens > 5

    def test_long_text(self):
        text = "word " * 1000
        tokens = count_tokens(text)
        assert 1000 <= tokens <= 1500


class TestMessageTokens:
    def test_single_message(self):
        msgs = [{"role": "user", "content": "Hello"}]
        tokens = count_message_tokens(msgs)
        assert tokens > 0

    def test_conversation(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Write code"},
            {"role": "assistant", "content": "def hello(): pass"},
        ]
        tokens = count_message_tokens(msgs)
        assert tokens > 10


class TestFitsContext:
    def test_fits(self):
        assert fits_context("short text", 1000)

    def test_doesnt_fit(self):
        assert not fits_context("x" * 10000, 10)


class TestTruncate:
    def test_no_truncation(self):
        text = "short"
        assert truncate_to_tokens(text, 1000) == text

    def test_truncation(self):
        text = "word " * 1000
        truncated = truncate_to_tokens(text, 100)
        assert len(truncated) < len(text)
        assert count_tokens(truncated) <= 100


class TestContextBudget:
    def test_budget(self):
        budget = context_budget(
            system_prompt="You are an agent." * 10,
            memory="User prefers pytest." * 5,
            skills="Review code quality." * 5,
        )
        assert budget["total_used"] > 0
        assert budget["remaining"] > 0
        assert budget["max_context"] == 100_000
        assert 0 <= budget["utilization"] <= 1

    def test_custom_max(self):
        budget = context_budget("x" * 4000, "", "", max_context=200)
        assert budget["remaining"] == 0
