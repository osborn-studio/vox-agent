"""Metrics — Prometheus-style metrics collection for monitoring dashboards.

Collects counters, gauges, and histograms for:
- Task throughput (tasks/minute)
- Agent utilization
- Tool call frequency
- API response times
- Error rates
"""

from __future__ import annotations

import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MetricPoint:
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class Counter:
    """Monotonically increasing counter."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._values: dict[str, float] = defaultdict(float)

    def inc(self, labels: dict[str, str] | None = None, amount: float = 1.0) -> None:
        key = _label_key(labels)
        self._values[key] += amount

    def get(self, labels: dict[str, str] | None = None) -> float:
        return self._values.get(_label_key(labels), 0.0)

    def all_values(self) -> dict[str, float]:
        return dict(self._values)


class Gauge:
    """Value that can go up and down."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._values: dict[str, float] = defaultdict(float)

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        self._values[_label_key(labels)] = value

    def inc(self, labels: dict[str, str] | None = None, amount: float = 1.0) -> None:
        self._values[_label_key(labels)] += amount

    def dec(self, labels: dict[str, str] | None = None, amount: float = 1.0) -> None:
        self._values[_label_key(labels)] -= amount

    def get(self, labels: dict[str, str] | None = None) -> float:
        return self._values.get(_label_key(labels), 0.0)


class Histogram:
    """Distribution of values (simplified: tracks count, sum, min, max)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._data: dict[str, dict] = {}

    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        if key not in self._data:
            self._data[key] = {"count": 0, "sum": 0.0, "min": float("inf"), "max": 0.0}
        d = self._data[key]
        d["count"] += 1
        d["sum"] += value
        d["min"] = min(d["min"], value)
        d["max"] = max(d["max"], value)

    def get_stats(self, labels: dict[str, str] | None = None) -> dict:
        key = _label_key(labels)
        d = self._data.get(key, {"count": 0, "sum": 0, "min": 0, "max": 0})
        avg = d["sum"] / d["count"] if d["count"] > 0 else 0
        return {**d, "avg": round(avg, 3)}


class MetricsRegistry:
    """Central metrics registry."""

    def __init__(self) -> None:
        # Task metrics
        self.tasks_created = Counter("tasks_created_total")
        self.tasks_completed = Counter("tasks_completed_total")
        self.tasks_failed = Counter("tasks_failed_total")
        self.active_tasks = Gauge("active_tasks")

        # Tool metrics
        self.tool_calls = Counter("tool_calls_total")
        self.tool_duration = Histogram("tool_duration_ms")

        # API metrics
        self.api_requests = Counter("api_requests_total")
        self.api_duration = Histogram("api_duration_ms")
        self.api_errors = Counter("api_errors_total")

        # Agent metrics
        self.agent_tasks = Counter("agent_tasks_total")

        # LLM metrics
        self.llm_calls = Counter("llm_calls_total")
        self.llm_tokens_in = Counter("llm_tokens_in_total")
        self.llm_tokens_out = Counter("llm_tokens_out_total")
        self.llm_cost = Counter("llm_cost_usd_total")

    def snapshot(self) -> dict[str, Any]:
        """Get current metrics snapshot."""
        return {
            "tasks": {
                "created": self.tasks_created.all_values(),
                "completed": self.tasks_completed.all_values(),
                "failed": self.tasks_failed.all_values(),
                "active": self.active_tasks.get(),
            },
            "tools": {
                "calls": self.tool_calls.all_values(),
                "duration": {k: self.tool_duration.get_stats({"tool": k})
                             for k in _extract_label_values(self.tool_calls, "tool")},
            },
            "api": {
                "requests": self.api_requests.all_values(),
                "errors": self.api_errors.all_values(),
                "duration": self.api_duration.get_stats(),
            },
            "llm": {
                "calls": self.llm_calls.all_values(),
                "tokens_in": self.llm_tokens_in.get(),
                "tokens_out": self.llm_tokens_out.get(),
                "cost_usd": round(self.llm_cost.get(), 4),
            },
        }


def _label_key(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


def _extract_label_values(counter: Counter, label: str) -> list[str]:
    values = set()
    for key in counter.all_values():
        for part in key.split(","):
            if part.startswith(f"{label}="):
                values.add(part.split("=", 1)[1])
    return sorted(values)


# Global registry
metrics = MetricsRegistry()
