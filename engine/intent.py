"""Intent Parser — classify user input into actionable intents.

This is the VoxLog-unique layer that Multica and OpenHarness don't have.
Converts natural language (from voice transcription) into structured actions.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    RECORD = "record"     # pure note-taking, no agent action
    TASK = "task"         # create and dispatch a task to an agent
    QUERY = "query"       # ask about task status or history
    CONFIRM = "confirm"   # accept/approve agent result
    CANCEL = "cancel"     # cancel running task
    CHAT = "chat"         # conversational exchange with agent


@dataclass
class IntentResult:
    intent: Intent
    confidence: float  # 0.0-1.0
    extracted_title: str = ""
    extracted_priority: str = "medium"
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Keyword-based fast path (no LLM needed)
# ---------------------------------------------------------------------------

CANCEL_PATTERNS = re.compile(
    r"(停下来|算了|取消|别做了|不用了|stop|cancel|abort|nevermind)",
    re.IGNORECASE,
)

CONFIRM_PATTERNS = re.compile(
    r"(^好的$|^可以了$|^通过$|^没问题$|^行$|^确认$|^ok$|^lgtm$|looks good|approved|ship it|^merge$)",
    re.IGNORECASE,
)

QUERY_PATTERNS = re.compile(
    r"(进度|怎么样了|做完了吗|^状态$|完成了吗|做到哪了|"
    r"^status\??$|progress|how.s it going|done yet|^update$)",
    re.IGNORECASE,
)

TASK_PATTERNS = re.compile(
    r"(帮我|请|麻烦|给我|写一个|修一下|修复|改一下|查一下|检查|测试|部署|"
    r"紧急|马上|立刻|尽快|"
    r"help me|please|fix|write|create|build|update|refactor|review|"
    r"check|test|run|deploy|implement|add|remove|delete|urgent|asap)",
    re.IGNORECASE,
)

RECORD_PATTERNS = re.compile(
    r"(记一下|备注|笔记|提醒我|记录|note|remind me|memo|jot down)",
    re.IGNORECASE,
)


class IntentParser:
    """Classify user input using keyword matching first, LLM fallback second.

    Priority order:
    1. Cancel (highest, immediate action)
    2. Confirm
    3. Query
    4. Record
    5. Task
    6. Chat (default fallback)
    """

    def parse(self, text: str, agent_id: str | None = None) -> IntentResult:
        """Synchronous keyword-based classification. No LLM needed."""
        text = text.strip()
        if not text:
            return IntentResult(intent=Intent.RECORD, confidence=0.0)

        # 1. Cancel — short, imperative
        if CANCEL_PATTERNS.search(text) and len(text) < 30:
            return IntentResult(
                intent=Intent.CANCEL,
                confidence=0.95,
                reasoning="cancel keyword detected",
            )

        # 2. Confirm — short, affirmative (must be very short to avoid false positives)
        if CONFIRM_PATTERNS.search(text) and len(text) < 20:
            return IntentResult(
                intent=Intent.CONFIRM,
                confidence=0.9,
                reasoning="confirm keyword detected",
            )

        # 3. Query — asking about status
        if QUERY_PATTERNS.search(text) and len(text) < 60:
            return IntentResult(
                intent=Intent.QUERY,
                confidence=0.85,
                reasoning="query keyword detected",
            )

        # 4. Record — explicit note-taking
        if RECORD_PATTERNS.search(text):
            return IntentResult(
                intent=Intent.RECORD,
                confidence=0.85,
                extracted_title=self._extract_note_title(text),
                reasoning="record keyword detected",
            )

        # 5. Task — imperative action request
        if TASK_PATTERNS.search(text):
            title = self._extract_task_title(text)
            priority = self._extract_priority(text)
            return IntentResult(
                intent=Intent.TASK,
                confidence=0.8,
                extracted_title=title,
                extracted_priority=priority,
                reasoning="task keyword detected",
            )

        # 6. Default: if talking to a specific agent, treat as chat
        if agent_id and agent_id != "notes":
            return IntentResult(
                intent=Intent.CHAT,
                confidence=0.6,
                reasoning="default: talking to agent",
            )

        # 7. No agent context → record
        return IntentResult(
            intent=Intent.RECORD,
            confidence=0.5,
            reasoning="default: no clear intent",
        )

    # -- extraction helpers --------------------------------------------------

    def _extract_task_title(self, text: str) -> str:
        """Extract a concise task title from the input."""
        # Remove common prefixes
        cleaned = re.sub(
            r"^(帮我|请|麻烦|给我|help me|please)\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        # Take first sentence or first 50 chars
        first_sentence = re.split(r"[。.!！？?\n]", cleaned)[0].strip()
        if len(first_sentence) > 50:
            first_sentence = first_sentence[:50] + "..."
        return first_sentence or text[:50]

    def _extract_note_title(self, text: str) -> str:
        """Extract note content from record intent."""
        cleaned = re.sub(
            r"^(记一下|备注|笔记|note|memo|jot down)[：:,，\s]*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return cleaned.strip()[:100]

    def _extract_priority(self, text: str) -> str:
        """Detect priority from keywords."""
        urgent_words = re.compile(r"(紧急|马上|立刻|urgent|asap|immediately|critical)", re.IGNORECASE)
        high_words = re.compile(r"(重要|尽快|important|high priority)", re.IGNORECASE)

        if urgent_words.search(text):
            return "urgent"
        if high_words.search(text):
            return "high"
        return "medium"
