"""Secret redaction utilities for work context ingest."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{12,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|token|password|secret)\s*=\s*['\"]?[^'\"\s]+"
    ),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
]


@dataclass
class RedactionResult:
    body: str
    reasons: list[str] = field(default_factory=list)
    source_hash: str = ""
    raw_retention_allowed: bool = True


def redact_secrets(text: str) -> RedactionResult:
    """Redact common secrets before text is persisted."""
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    redacted = text
    reasons: list[str] = []

    for pattern in SECRET_PATTERNS:
        redacted, count = pattern.subn("[REDACTED:secret]", redacted)
        if count and "secret" not in reasons:
            reasons.append("secret")

    return RedactionResult(
        body=redacted,
        reasons=reasons,
        source_hash=source_hash,
        raw_retention_allowed=not reasons,
    )
