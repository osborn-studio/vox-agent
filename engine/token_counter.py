"""Token Counter — estimate token counts for context window management.

Uses character-based estimation (4 chars ≈ 1 token for English,
2 chars ≈ 1 token for Chinese) to help stay within LLM context limits.
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# Rough ratios
ENGLISH_CHARS_PER_TOKEN = 4.0
CHINESE_CHARS_PER_TOKEN = 1.5
CODE_CHARS_PER_TOKEN = 3.5

# CJK character range
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def count_tokens(text: str) -> int:
    """Estimate token count for a text string."""
    if not text:
        return 0

    cjk_chars = len(CJK_PATTERN.findall(text))
    non_cjk_chars = len(text) - cjk_chars

    # Estimate tokens
    cjk_tokens = cjk_chars / CHINESE_CHARS_PER_TOKEN
    non_cjk_tokens = non_cjk_chars / ENGLISH_CHARS_PER_TOKEN

    return int(cjk_tokens + non_cjk_tokens)


def count_message_tokens(messages: list[dict]) -> int:
    """Estimate tokens for a list of chat messages."""
    total = 0
    for msg in messages:
        total += 4  # message overhead
        total += count_tokens(msg.get("content", ""))
        total += count_tokens(msg.get("role", ""))
    total += 2  # conversation overhead
    return total


def fits_context(text: str, max_tokens: int) -> bool:
    """Check if text fits within a token budget."""
    return count_tokens(text) <= max_tokens


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    if count_tokens(text) <= max_tokens:
        return text

    # Binary search for the right length
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if count_tokens(text[:mid]) <= max_tokens:
            low = mid
        else:
            high = mid - 1

    return text[:low]


def context_budget(
    system_prompt: str,
    memory: str,
    skills: str,
    max_context: int = 100_000,
) -> dict:
    """Calculate remaining token budget after fixed costs."""
    system_tokens = count_tokens(system_prompt)
    memory_tokens = count_tokens(memory)
    skills_tokens = count_tokens(skills)
    used = system_tokens + memory_tokens + skills_tokens
    remaining = max(0, max_context - used)

    return {
        "system_prompt": system_tokens,
        "memory": memory_tokens,
        "skills": skills_tokens,
        "total_used": used,
        "remaining": remaining,
        "max_context": max_context,
        "utilization": round(used / max_context, 3) if max_context > 0 else 0,
    }
