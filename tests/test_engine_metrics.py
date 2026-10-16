"""Tests for engine.metrics — Prometheus-style metrics collection."""

from engine.metrics import Counter, Gauge, Histogram, MetricsRegistry


class TestCounter:
    def test_inc(self):
        c = Counter("test")
        c.inc()
        c.inc()
        assert c.get() == 2.0

    def test_inc_amount(self):
        c = Counter("test")
        c.inc(amount=5)
        assert c.get() == 5.0

    def test_labels(self):
        c = Counter("test")
        c.inc({"agent": "claude"})
        c.inc({"agent": "codex"})
        c.inc({"agent": "claude"})
        assert c.get({"agent": "claude"}) == 2.0
        assert c.get({"agent": "codex"}) == 1.0

    def test_all_values(self):
        c = Counter("test")
        c.inc({"status": "ok"})
        c.inc({"status": "error"})
        vals = c.all_values()
        assert len(vals) == 2


class TestGauge:
    def test_set(self):
        g = Gauge("test")
        g.set(42)
        assert g.get() == 42

    def test_inc_dec(self):
        g = Gauge("test")
        g.inc()
        g.inc()
        g.dec()
        assert g.get() == 1.0

    def test_labels(self):
        g = Gauge("test")
        g.set(10, {"agent": "claude"})
        g.set(5, {"agent": "codex"})
        assert g.get({"agent": "claude"}) == 10
        assert g.get({"agent": "codex"}) == 5


class TestHistogram:
    def test_observe(self):
        h = Histogram("test")
        h.observe(10)
        h.observe(20)
        h.observe(30)
        stats = h.get_stats()
        assert stats["count"] == 3
        assert stats["min"] == 10
        assert stats["max"] == 30
        assert stats["avg"] == 20.0

    def test_single(self):
        h = Histogram("test")
        h.observe(42)
        stats = h.get_stats()
        assert stats["count"] == 1
        assert stats["avg"] == 42.0

    def test_empty(self):
        h = Histogram("test")
        stats = h.get_stats()
        assert stats["count"] == 0


class TestMetricsRegistry:
    def test_snapshot(self):
        reg = MetricsRegistry()
        reg.tasks_created.inc({"agent": "claude"})
        reg.tasks_completed.inc({"agent": "claude"})
        reg.active_tasks.set(2)
        reg.tool_calls.inc({"tool": "bash"})
        reg.api_requests.inc()

        snap = reg.snapshot()
        assert "tasks" in snap
        assert "tools" in snap
        assert "api" in snap
        assert "llm" in snap

    def test_llm_tracking(self):
        reg = MetricsRegistry()
        reg.llm_calls.inc()
        reg.llm_tokens_in.inc(amount=1000)
        reg.llm_tokens_out.inc(amount=500)
        reg.llm_cost.inc(amount=0.05)

        snap = reg.snapshot()
        assert snap["llm"]["tokens_in"] == 1000
        assert snap["llm"]["cost_usd"] == 0.05
