"""Retry logic — exponential backoff for transient failures.

Used by the scheduler to auto-retry tasks that fail due to
network issues, rate limits, or temporary API errors.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Errors considered transient (worth retrying)
TRANSIENT_ERRORS = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,
)

# Error message patterns that indicate rate limiting
RATE_LIMIT_PATTERNS = [
    "rate limit",
    "too many requests",
    "429",
    "quota exceeded",
    "overloaded",
]


@dataclass
class RetryConfig:
    max_retries: int = 3
    base_delay: float = 1.0   # seconds
    max_delay: float = 32.0   # cap
    backoff_factor: float = 2.0


def is_transient(error: Exception) -> bool:
    """Check if an error is likely transient and worth retrying."""
    if isinstance(error, TRANSIENT_ERRORS):
        return True
    # httpx is a runtime dependency, but keep this helper usable in minimal
    # environments (for example standalone retry unit tests).
    try:
        import httpx

        if isinstance(error, httpx.TransportError):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status in {408, 425, 429} or status >= 500:
                return True
    except ImportError:
        pass
    msg = str(error).lower()
    return any(pattern in msg for pattern in RATE_LIMIT_PATTERNS)


async def with_retry(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    config: RetryConfig | None = None,
    on_retry: Callable[[int, Exception, float], Awaitable[None]] | None = None,
    sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
    **kwargs: Any,
) -> T:
    """Execute an async function with exponential backoff retry.

    Args:
        func: Async function to execute
        config: Retry configuration
        on_retry: Callback(attempt, error, delay) called before each retry
        sleep_func: Awaitable delay function; injectable for deterministic tests

    Returns:
        Result of func(*args, **kwargs)

    Raises:
        The last exception if all retries exhausted
    """
    cfg = config or RetryConfig()
    last_error: Exception | None = None

    for attempt in range(cfg.max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_error = e

            if attempt >= cfg.max_retries:
                break

            if not is_transient(e):
                logger.info("non-transient error, not retrying: %s", e)
                raise

            delay = min(
                cfg.base_delay * (cfg.backoff_factor ** attempt),
                cfg.max_delay,
            )

            logger.warning(
                "retry %d/%d in %.1fs: %s",
                attempt + 1, cfg.max_retries, delay, e,
            )

            if on_retry:
                await on_retry(attempt + 1, e, delay)

            await sleep_func(delay)

    assert last_error is not None
    raise last_error
