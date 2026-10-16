"""Lightweight text corrector — runs in fast path (<50ms).

Only does:
- Dictionary term replacement (case-insensitive)
- Chinese-English spacing
- Basic formatting

Does NOT do:
- Sentence rewriting
- LLM calls
- Semantic analysis
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

logger = structlog.get_logger()


class Corrector:
    def __init__(self):
        self.corrections: dict[str, str] = {}
        self.preserve: list[str] = []
        self.cn_en_space: bool = True

    def load(self, *paths: Path) -> None:
        """Load and merge dictionaries from multiple files (base → technical → personal)."""
        for path in paths:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.corrections.update(data.get("corrections", {}))
                self.preserve.extend(data.get("preserve", []))
                rules = data.get("format_rules", {})
                if "cn_en_space" in rules:
                    self.cn_en_space = rules["cn_en_space"]
                logger.info("corrector.loaded", path=str(path), terms=len(data.get("corrections", {})))
            except Exception as e:
                logger.warning("corrector.load_fail", path=str(path), error=str(e))

    def correct_with_trace(self, text: str) -> tuple[str, list[dict[str, str]]]:
        """Apply lightweight corrections and return the replacements made."""
        if not text:
            return text, []

        result = text
        applied: list[dict[str, str]] = []

        # Step 1: Dictionary corrections (case-insensitive)
        for wrong, right in self.corrections.items():
            pattern = re.compile(re.escape(wrong), re.IGNORECASE)
            if pattern.search(result):
                applied.append({"from": wrong, "to": right})
            result = pattern.sub(right, result)

        # Step 2: Chinese-English spacing
        if self.cn_en_space:
            result = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9])", r"\1 \2", result)
            result = re.sub(r"([A-Za-z0-9])([\u4e00-\u9fff])", r"\1 \2", result)

        return result, applied

    def correct(self, text: str) -> str:
        """Apply lightweight corrections. Must complete in <50ms."""
        corrected, _ = self.correct_with_trace(text)
        return corrected
