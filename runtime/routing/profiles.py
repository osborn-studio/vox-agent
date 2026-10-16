"""Network profile detection and switching.

Provider-aware health strategy instead of single google.com DNS check.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import structlog

from runtime.models.config import VoxLogConfig

logger = structlog.get_logger()

# Cache
_detected_profile: str | None = None
_detect_time: float = 0
_CACHE_TTL = 300  # 5 minutes


async def detect_profile(config: VoxLogConfig) -> str:
    """Detect best profile based on provider reachability."""
    global _detected_profile, _detect_time

    now = time.monotonic()
    if _detected_profile and (now - _detect_time) < _CACHE_TTL:
        return _detected_profile

    profile = await _do_detect(config)
    _detected_profile = profile
    _detect_time = now
    config.switch_profile(profile)
    logger.info("profile.detected", profile=profile)
    return profile


async def _do_detect(config: VoxLogConfig) -> str:
    """Check which providers are reachable to determine profile."""
    checks = {
        "home": _check_url("https://api.openai.com/v1/models", config.openai_key),
        "office": _check_url("https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation", None),
    }

    results = {}
    for name, coro in checks.items():
        try:
            ms = await asyncio.wait_for(coro, timeout=3.0)
            results[name] = ms
            logger.debug("profile.check", name=name, ms=ms)
        except (asyncio.TimeoutError, Exception):
            results[name] = 99999

    # Pick the one with lower latency
    if results.get("home", 99999) < results.get("office", 99999):
        return "home"
    return "office"


async def _check_url(url: str, key: str | None) -> int:
    """Check if a URL is reachable. Returns latency in ms."""
    start = time.monotonic()
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=3.0) as c:
        # Any response (even 401/405) means the endpoint is reachable
        await c.head(url, headers=headers)
        return int((time.monotonic() - start) * 1000)


def invalidate_cache():
    global _detected_profile, _detect_time
    _detected_profile = None
    _detect_time = 0
