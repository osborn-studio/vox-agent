"""Conversation Manager — track multi-turn interactions per agent.

Maintains conversation windows for agents, enabling context-aware
responses across multiple task executions.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ConversationMessage:
    role: str  # "user" | "agent" | "system"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    task_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ConversationManager:
    """Maintain conversation context per agent.

    Keeps a sliding window of recent messages for each agent,
    enabling multi-turn conversations where agents can refer
    back to previous exchanges.
    """

    def __init__(self, max_messages_per_agent: int = 50) -> None:
        self._conversations: dict[str, deque[ConversationMessage]] = {}
        self._max = max_messages_per_agent

    def add_user_message(self, agent_id: str, content: str, task_id: str = "") -> None:
        self._ensure(agent_id)
        self._conversations[agent_id].append(ConversationMessage(
            role="user", content=content, task_id=task_id,
        ))

    def add_agent_message(self, agent_id: str, content: str, task_id: str = "") -> None:
        self._ensure(agent_id)
        self._conversations[agent_id].append(ConversationMessage(
            role="agent", content=content, task_id=task_id,
        ))

    def add_system_message(self, agent_id: str, content: str) -> None:
        self._ensure(agent_id)
        self._conversations[agent_id].append(ConversationMessage(
            role="system", content=content,
        ))

    def get_history(self, agent_id: str, limit: int | None = None) -> list[ConversationMessage]:
        """Get conversation history for an agent."""
        msgs = list(self._conversations.get(agent_id, []))
        if limit:
            msgs = msgs[-limit:]
        return msgs

    def get_context_messages(self, agent_id: str, limit: int = 10) -> list[dict]:
        """Get history formatted for LLM context injection."""
        msgs = self.get_history(agent_id, limit)
        return [
            {"role": m.role if m.role != "agent" else "assistant", "content": m.content}
            for m in msgs
        ]

    def clear(self, agent_id: str) -> None:
        self._conversations.pop(agent_id, None)

    def clear_all(self) -> None:
        self._conversations.clear()

    def active_conversations(self) -> list[str]:
        return [aid for aid, msgs in self._conversations.items() if msgs]

    def message_count(self, agent_id: str) -> int:
        return len(self._conversations.get(agent_id, []))

    def _ensure(self, agent_id: str) -> None:
        if agent_id not in self._conversations:
            self._conversations[agent_id] = deque(maxlen=self._max)
