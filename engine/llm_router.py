"""LLM Router — intelligent model selection based on task complexity.

Routes tasks to the cheapest model that can handle them:
- Simple tasks → fast/cheap models (GPT-4o-mini, Qwen-turbo)
- Complex tasks → capable models (Claude Opus, GPT-4o)
- Code tasks → code-specialized models

Factors: task length, keyword complexity, estimated difficulty.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ModelRoute:
    model: str
    provider: str
    reason: str
    estimated_cost_per_1k: float  # USD per 1K tokens


# Model tiers
MODELS = {
    "fast": ModelRoute("gpt-4o-mini", "openai", "simple/short task", 0.00015),
    "balanced": ModelRoute("claude-sonnet-5", "claude", "moderate complexity", 0.003),
    "powerful": ModelRoute("claude-opus-4-8", "claude", "complex reasoning", 0.005),
    "code": ModelRoute("claude-sonnet-5", "claude", "code-focused task", 0.003),
    "cheap_cn": ModelRoute("qwen-turbo", "qwen", "Chinese text, low cost", 0.0003),
    "deepseek": ModelRoute("deepseek-chat", "deepseek", "reasoning + cheap", 0.00014),
}

# Complexity indicators
COMPLEX_PATTERNS = re.compile(
    r"(architect|design|refactor|migrate|security audit|performance|"
    r"重构|架构|迁移|安全审计|性能优化)", re.IGNORECASE
)
CODE_PATTERNS = re.compile(
    r"(code|fix|bug|test|implement|function|class|module|API|"
    r"\.(py|ts|js|swift|rs|go|java)\b)", re.IGNORECASE
)
SIMPLE_PATTERNS = re.compile(
    r"(list|show|status|check|count|format|rename|"
    r"查看|列出|状态|检查|格式化)", re.IGNORECASE
)
CJK_HEAVY = re.compile(r"[\u4e00-\u9fff]{10,}")


class LLMRouter:
    """Route tasks to the optimal LLM model."""

    def __init__(self, default_provider: str = "claude") -> None:
        self.default_provider = default_provider
        self._overrides: dict[str, str] = {}  # agent_id → model tier

    def set_override(self, agent_id: str, tier: str) -> None:
        """Force a specific model tier for an agent."""
        self._overrides[agent_id] = tier

    def route(self, prompt: str, agent_id: str = "") -> ModelRoute:
        """Select the best model for a task prompt."""
        # Check agent-level override
        if agent_id and agent_id in self._overrides:
            tier = self._overrides[agent_id]
            return MODELS.get(tier, MODELS["balanced"])

        # Analyze prompt
        length = len(prompt)
        is_complex = bool(COMPLEX_PATTERNS.search(prompt))
        is_code = bool(CODE_PATTERNS.search(prompt))
        is_simple = bool(SIMPLE_PATTERNS.search(prompt))
        is_chinese = bool(CJK_HEAVY.search(prompt))

        # Route decision
        if is_simple and length < 200:
            return MODELS["fast"]
        if is_complex or length > 5000:
            return MODELS["powerful"]
        if is_code:
            return MODELS["code"]
        if is_chinese and not is_complex:
            return MODELS["cheap_cn"]

        return MODELS["balanced"]

    def estimate_cost(self, prompt: str, agent_id: str = "") -> dict:
        """Estimate cost for a task before execution."""
        route = self.route(prompt, agent_id)
        token_estimate = len(prompt) // 4 + 500  # input + expected output
        cost = route.estimated_cost_per_1k * token_estimate / 1000

        return {
            "model": route.model,
            "provider": route.provider,
            "reason": route.reason,
            "estimated_tokens": token_estimate,
            "estimated_cost_usd": round(cost, 6),
        }

    def available_tiers(self) -> list[dict]:
        return [
            {"tier": k, "model": v.model, "provider": v.provider, "cost_per_1k": v.estimated_cost_per_1k}
            for k, v in MODELS.items()
        ]
