"""Rate Limiter — protect LLM APIs from excessive calls.

Token bucket algorithm with per-provider limits.
Prevents runaway agents from burning API credits.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    requests_per_minute: int = 60
    tokens_per_minute: int = 100_000
    max_burst: int = 10


class TokenBucket:
    """Token bucket rate limiter."""

    def __init__(self, rate: float, capacity: int) -> None:
        self.rate = rate  # tokens per second
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> float:
        """Acquire tokens. Returns wait time in seconds (0 if immediate)."""
        async with self._lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0.0
            deficit = tokens - self.tokens
            wait = deficit / self.rate
            self.tokens = 0
            return wait

    async def wait_and_acquire(self, tokens: int = 1) -> None:
        """Acquire tokens, waiting if necessary."""
        wait = await self.acquire(tokens)
        if wait > 0:
            logger.debug("rate limit: waiting %.2fs", wait)
            await asyncio.sleep(wait)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

    @property
    def available(self) -> int:
        self._refill()
        return int(self.tokens)


class RateLimiter:
    """Per-provider rate limiting for LLM API calls."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._configs: dict[str, RateLimitConfig] = {}
        self._usage: dict[str, dict] = {}

    def configure(self, provider: str, config: RateLimitConfig) -> None:
        """Set rate limits for a provider."""
        self._configs[provider] = config
        self._buckets[provider] = TokenBucket(
            rate=config.requests_per_minute / 60.0,
            capacity=config.max_burst,
        )
        self._usage[provider] = {"requests": 0, "tokens": 0, "last_reset": time.time()}

    async def check(self, provider: str) -> bool:
        """Check if a request is allowed. Returns True if OK."""
        bucket = self._buckets.get(provider)
        if not bucket:
            return True  # no limit configured
        wait = await bucket.acquire()
        return wait == 0.0

    async def wait(self, provider: str) -> None:
        """Wait until a request is allowed."""
        bucket = self._buckets.get(provider)
        if not bucket:
            return
        await bucket.wait_and_acquire()

    def record_usage(self, provider: str, tokens: int = 0) -> None:
        """Record API usage for tracking."""
        if provider not in self._usage:
            self._usage[provider] = {"requests": 0, "tokens": 0, "last_reset": time.time()}

        usage = self._usage[provider]
        usage["requests"] += 1
        usage["tokens"] += tokens

        # Reset every minute
        if time.time() - usage["last_reset"] > 60:
            usage["requests"] = 1
            usage["tokens"] = tokens
            usage["last_reset"] = time.time()

    def get_usage(self, provider: str) -> dict:
        """Get current usage stats for a provider."""
        usage = self._usage.get(provider, {})
        config = self._configs.get(provider)
        bucket = self._buckets.get(provider)
        return {
            "provider": provider,
            "requests_this_minute": usage.get("requests", 0),
            "tokens_this_minute": usage.get("tokens", 0),
            "limit_rpm": config.requests_per_minute if config else None,
            "limit_tpm": config.tokens_per_minute if config else None,
            "available_burst": bucket.available if bucket else None,
        }

    def all_usage(self) -> list[dict]:
        providers = set(list(self._configs.keys()) + list(self._usage.keys()))
        return [self.get_usage(p) for p in sorted(providers)]
