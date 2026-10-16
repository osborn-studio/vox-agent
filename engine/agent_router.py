"""Agent Router — automatically select the best agent for a task.

When the user doesn't specify an agent, the router picks one based on:
- Task content analysis (keywords, file types mentioned)
- Agent capabilities (skills, provider)
- Agent availability (online, not overloaded)
- Historical performance (success rate for similar tasks)
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

from .agent_config import AgentConfigScanner
from .heartbeat import HeartbeatMonitor
from .analytics import TaskAnalytics

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    agent_id: str
    confidence: float  # 0.0-1.0
    reason: str


# Keyword patterns → preferred agent
AGENT_KEYWORDS = {
    "claude-code": [
        r"(code|write|fix|bug|test|refactor|review|PR|commit|git|deploy)",
        r"(代码|写|修|测试|重构|审查|提交|部署)",
        r"\.(py|ts|js|swift|rs|go|java|rb|cpp|c)\b",
    ],
    "codex": [
        r"(generate|complete|suggest|autocomplete)",
        r"(生成|补全|建议)",
    ],
    "openclaw": [
        r"(小龙虾|openclaw|claw)",
    ],
    "osborn": [
        r"(osborn|系统|架构|design|architect)",
    ],
    "general": [
        r"(note|记|备注|memo|reminder)",
    ],
}


class AgentRouter:
    """Select the best agent for a given task."""

    def __init__(
        self,
        scanner: AgentConfigScanner,
        heartbeat: HeartbeatMonitor | None = None,
        analytics: TaskAnalytics | None = None,
    ) -> None:
        self.scanner = scanner
        self.heartbeat = heartbeat
        self.analytics = analytics
        self._compiled: dict[str, list[re.Pattern]] = {}
        self._compile_keywords()

    def _compile_keywords(self) -> None:
        for agent_id, patterns in AGENT_KEYWORDS.items():
            self._compiled[agent_id] = [
                re.compile(p, re.IGNORECASE) for p in patterns
            ]

    def route(self, text: str, preferred_agent: str | None = None) -> RouteResult:
        """Select the best agent for the given task text."""
        # 1. If user specified an agent, use it
        if preferred_agent:
            return RouteResult(
                agent_id=preferred_agent,
                confidence=1.0,
                reason="user specified",
            )

        # 2. Keyword matching
        scores: dict[str, float] = {}
        for agent_id, patterns in self._compiled.items():
            score = 0.0
            for pattern in patterns:
                if pattern.search(text):
                    score += 0.3
            if score > 0:
                scores[agent_id] = min(score, 0.9)

        # 3. Check agent availability (boost online agents)
        if self.heartbeat:
            for agent_id in list(scores.keys()):
                if self.heartbeat.is_online(agent_id):
                    scores[agent_id] += 0.1

        # 4. Check historical performance (boost high success rate)
        if self.analytics:
            for agent_id in list(scores.keys()):
                stats = self.analytics.agent_stats(agent_id)
                if stats.total_tasks > 5 and stats.success_rate > 0.8:
                    scores[agent_id] += 0.1

        # 5. Pick the best or default
        if scores:
            best = max(scores, key=scores.get)
            return RouteResult(
                agent_id=best,
                confidence=scores[best],
                reason="keyword match" + (" + online" if self.heartbeat and self.heartbeat.is_online(best) else ""),
            )

        # 6. Default to claude-code
        return RouteResult(
            agent_id="claude-code",
            confidence=0.5,
            reason="default (no keyword match)",
        )

    def suggest_agents(self, text: str, top_n: int = 3) -> list[RouteResult]:
        """Return top N agent suggestions for a task."""
        scores: dict[str, float] = {}
        for agent_id, patterns in self._compiled.items():
            score = 0.0
            for pattern in patterns:
                if pattern.search(text):
                    score += 0.3
            scores[agent_id] = min(score, 0.9)

        sorted_agents = sorted(scores.items(), key=lambda x: -x[1])[:top_n]
        return [
            RouteResult(agent_id=aid, confidence=score, reason="keyword match")
            for aid, score in sorted_agents
            if score > 0
        ]
