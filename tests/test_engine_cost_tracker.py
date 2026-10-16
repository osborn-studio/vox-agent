"""Tests for engine.cost_tracker — API cost monitoring."""

import pytest
from engine.cost_tracker import CostTracker, PRICING


@pytest.fixture
def tracker(tmp_path):
    return CostTracker(tmp_path / "costs")


class TestCostTracking:
    def test_record_cost(self, tracker):
        rec = tracker.record(
            task_id="t1", agent_id="claude",
            model="claude-opus-4-6", input_tokens=1000, output_tokens=500,
            cost_usd=0.05,
        )
        assert rec.cost_usd == 0.05

    def test_auto_estimate_cost(self, tracker):
        rec = tracker.record(
            task_id="t1", agent_id="claude",
            model="gpt-4o", input_tokens=10000, output_tokens=5000,
        )
        assert rec.cost_usd > 0  # should auto-calculate

    def test_today_cost(self, tracker):
        tracker.record(task_id="t1", agent_id="a", cost_usd=0.10)
        tracker.record(task_id="t2", agent_id="b", cost_usd=0.20)
        assert tracker.today_cost() == pytest.approx(0.30)

    def test_budget_check(self, tracker):
        tracker.set_budget(0.50)
        tracker.record(task_id="t1", agent_id="a", cost_usd=0.30)
        assert not tracker.is_over_budget()
        tracker.record(task_id="t2", agent_id="a", cost_usd=0.25)
        assert tracker.is_over_budget()

    def test_no_budget(self, tracker):
        tracker.record(task_id="t1", agent_id="a", cost_usd=100.0)
        assert not tracker.is_over_budget()


class TestCostSummary:
    def test_empty_summary(self, tracker):
        s = tracker.summary()
        assert s.total_cost_usd == 0
        assert s.total_requests == 0

    def test_summary_by_agent(self, tracker):
        tracker.record(task_id="t1", agent_id="claude", cost_usd=0.10)
        tracker.record(task_id="t2", agent_id="claude", cost_usd=0.20)
        tracker.record(task_id="t3", agent_id="codex", cost_usd=0.05)
        s = tracker.summary()
        assert s.by_agent["claude"] == pytest.approx(0.30)
        assert s.by_agent["codex"] == pytest.approx(0.05)

    def test_summary_by_model(self, tracker):
        tracker.record(task_id="t1", agent_id="a", model="gpt-4o", cost_usd=0.10)
        tracker.record(task_id="t2", agent_id="a", model="claude-opus-4-6", cost_usd=0.50)
        s = tracker.summary()
        assert "gpt-4o" in s.by_model
        assert "claude-opus-4-6" in s.by_model


class TestClaudeResultParsing:
    def test_parse_claude_result(self, tracker):
        result = {
            "total_cost_usd": 0.40750875,
            "duration_ms": 4884,
            "usage": {
                "input_tokens": 3,
                "output_tokens": 5,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 65179,
            },
            "modelUsage": {
                "claude-opus-4-6[1m]": {
                    "inputTokens": 3,
                    "outputTokens": 5,
                    "costUSD": 0.40750875,
                }
            },
        }
        rec = tracker.record_from_claude_result("task-1", "claude-code", result)
        assert rec is not None
        assert rec.cost_usd == pytest.approx(0.4075, abs=0.001)
        assert rec.model == "claude-opus-4-6[1m]"
        assert rec.input_tokens == 3
        assert rec.output_tokens == 5

    def test_parse_empty_result(self, tracker):
        assert tracker.record_from_claude_result("t", "a", "not a dict") is None


class TestPersistence:
    def test_save_and_reload(self, tmp_path):
        t1 = CostTracker(tmp_path / "costs")
        t1.record(task_id="t1", agent_id="claude", cost_usd=0.10)
        t1.record(task_id="t2", agent_id="claude", cost_usd=0.20)

        t2 = CostTracker(tmp_path / "costs")
        assert t2.today_cost() == pytest.approx(0.30)


class TestPricing:
    def test_known_models(self):
        assert "claude-opus-4-6" in PRICING
        assert "gpt-4o" in PRICING
        assert "deepseek-chat" in PRICING

    def test_estimate(self, tracker):
        rec = tracker.record(
            task_id="t", agent_id="a",
            model="gpt-4o-mini",
            input_tokens=1_000_000,
            output_tokens=500_000,
        )
        # 1M * $0.15/M + 500K * $0.60/M = $0.15 + $0.30 = $0.45
        assert rec.cost_usd == pytest.approx(0.45, abs=0.01)
