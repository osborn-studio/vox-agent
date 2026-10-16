"""Cost Tracker — monitor LLM API spend per agent and per task.

Tracks token usage and estimated cost from claude CLI output.
Provides daily/weekly/monthly summaries and budget alerts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)

# Approximate pricing per 1M tokens (USD)
PRICING = {
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-5": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_write": 3.75, "cache_read": 1.50},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "qwen-turbo": {"input": 0.30, "output": 0.60},
}


@dataclass
class UsageRecord:
    timestamp: str
    task_id: str
    agent_id: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0


@dataclass
class CostSummary:
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_requests: int
    by_agent: dict[str, float]
    by_model: dict[str, float]


class CostTracker:
    """Track and report API costs."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or (resolve_vox_agent_data_dir() / "costs")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._records: list[UsageRecord] = []
        self._budget_usd: float = 0.0  # 0 = no budget
        self._load()

    def record(
        self,
        task_id: str,
        agent_id: str,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
    ) -> UsageRecord:
        """Record an API call's cost."""
        # Calculate cost if not provided
        if cost_usd == 0 and model:
            cost_usd = self._estimate_cost(
                model, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens,
            )

        rec = UsageRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            task_id=task_id,
            agent_id=agent_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
        )
        self._records.append(rec)
        self._save_append(rec)

        logger.info(
            "cost: $%.4f (%s, %d+%d tokens, task=%s)",
            cost_usd, model, input_tokens, output_tokens, task_id[:8],
        )
        return rec

    def record_from_claude_result(self, task_id: str, agent_id: str, result: dict) -> UsageRecord | None:
        """Extract cost info from claude CLI result JSON."""
        if not isinstance(result, dict):
            return None

        cost = result.get("total_cost_usd", 0)
        usage = result.get("usage", {})
        duration = result.get("duration_ms", 0)

        # Find model from modelUsage
        model = ""
        model_usage = result.get("modelUsage", {})
        if model_usage:
            model = next(iter(model_usage.keys()), "")

        return self.record(
            task_id=task_id,
            agent_id=agent_id,
            model=model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
            cost_usd=cost,
            duration_ms=duration,
        )

    def set_budget(self, daily_usd: float) -> None:
        self._budget_usd = daily_usd

    def summary(self, days: int = 30) -> CostSummary:
        """Get cost summary for the last N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        recent = [r for r in self._records if r.timestamp >= cutoff]

        by_agent: dict[str, float] = {}
        by_model: dict[str, float] = {}

        for r in recent:
            by_agent[r.agent_id] = by_agent.get(r.agent_id, 0) + r.cost_usd
            if r.model:
                by_model[r.model] = by_model.get(r.model, 0) + r.cost_usd

        return CostSummary(
            total_cost_usd=sum(r.cost_usd for r in recent),
            total_input_tokens=sum(r.input_tokens for r in recent),
            total_output_tokens=sum(r.output_tokens for r in recent),
            total_requests=len(recent),
            by_agent=by_agent,
            by_model=by_model,
        )

    def today_cost(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        return sum(r.cost_usd for r in self._records if r.timestamp.startswith(today))

    def is_over_budget(self) -> bool:
        if self._budget_usd <= 0:
            return False
        return self.today_cost() >= self._budget_usd

    def _estimate_cost(
        self, model: str, input_t: int, output_t: int,
        cache_read: int, cache_write: int,
    ) -> float:
        # Find pricing (try exact match, then prefix)
        pricing = PRICING.get(model)
        if not pricing:
            for key in PRICING:
                if key in model or model in key:
                    pricing = PRICING[key]
                    break
        if not pricing:
            return 0.0

        cost = (
            input_t * pricing.get("input", 0) / 1_000_000
            + output_t * pricing.get("output", 0) / 1_000_000
            + cache_read * pricing.get("cache_read", 0) / 1_000_000
            + cache_write * pricing.get("cache_write", 0) / 1_000_000
        )
        return round(cost, 6)

    def _save_append(self, rec: UsageRecord) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.data_dir / f"costs-{today}.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps({
                "ts": rec.timestamp, "task": rec.task_id, "agent": rec.agent_id,
                "model": rec.model, "in": rec.input_tokens, "out": rec.output_tokens,
                "cost": rec.cost_usd, "ms": rec.duration_ms,
            }) + "\n")

    def _load(self) -> None:
        for path in sorted(self.data_dir.glob("costs-*.jsonl")):
            try:
                for line in path.read_text().splitlines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    self._records.append(UsageRecord(
                        timestamp=d["ts"], task_id=d["task"], agent_id=d["agent"],
                        model=d.get("model", ""), input_tokens=d.get("in", 0),
                        output_tokens=d.get("out", 0), cost_usd=d.get("cost", 0),
                        duration_ms=d.get("ms", 0),
                    ))
            except Exception:
                logger.exception("failed to load %s", path)
