"""Tests for engine.rate_limiter — API rate limiting."""

import pytest
from engine.rate_limiter import RateLimiter, RateLimitConfig, TokenBucket


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_immediate_acquire(self):
        bucket = TokenBucket(rate=10, capacity=10)
        wait = await bucket.acquire(1)
        assert wait == 0.0

    @pytest.mark.asyncio
    async def test_burst_capacity(self):
        bucket = TokenBucket(rate=1, capacity=5)
        for _ in range(5):
            wait = await bucket.acquire(1)
            assert wait == 0.0
        # 6th should require wait
        wait = await bucket.acquire(1)
        assert wait > 0

    @pytest.mark.asyncio
    async def test_available(self):
        bucket = TokenBucket(rate=10, capacity=10)
        assert bucket.available == 10
        await bucket.acquire(3)
        assert bucket.available == 7


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_no_config_allows_all(self):
        limiter = RateLimiter()
        assert await limiter.check("unconfigured")

    @pytest.mark.asyncio
    async def test_configured_provider(self):
        limiter = RateLimiter()
        limiter.configure("openai", RateLimitConfig(requests_per_minute=60, max_burst=5))
        assert await limiter.check("openai")

    @pytest.mark.asyncio
    async def test_burst_exceeded(self):
        limiter = RateLimiter()
        limiter.configure("openai", RateLimitConfig(requests_per_minute=60, max_burst=2))

        assert await limiter.check("openai")
        assert await limiter.check("openai")
        assert not await limiter.check("openai")  # burst exceeded

    def test_record_usage(self):
        limiter = RateLimiter()
        limiter.record_usage("claude", tokens=500)
        limiter.record_usage("claude", tokens=300)
        usage = limiter.get_usage("claude")
        assert usage["requests_this_minute"] == 2
        assert usage["tokens_this_minute"] == 800

    def test_all_usage(self):
        limiter = RateLimiter()
        limiter.configure("openai", RateLimitConfig())
        limiter.record_usage("openai")
        limiter.record_usage("claude")
        usage = limiter.all_usage()
        assert len(usage) == 2

    def test_usage_with_limits(self):
        limiter = RateLimiter()
        limiter.configure("openai", RateLimitConfig(requests_per_minute=100, tokens_per_minute=50000))
        usage = limiter.get_usage("openai")
        assert usage["limit_rpm"] == 100
        assert usage["limit_tpm"] == 50000
