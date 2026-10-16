"""Tests for engine.retry — exponential backoff retry logic."""

import httpx
import pytest
from engine.retry import with_retry, RetryConfig, is_transient


class TestIsTransient:
    def test_connection_error(self):
        assert is_transient(ConnectionError("refused"))

    def test_timeout_error(self):
        assert is_transient(TimeoutError("timed out"))

    def test_rate_limit_message(self):
        assert is_transient(Exception("rate limit exceeded"))

    def test_429_message(self):
        assert is_transient(Exception("HTTP 429 Too Many Requests"))

    def test_non_transient(self):
        assert not is_transient(ValueError("invalid input"))

    def test_non_transient_key_error(self):
        assert not is_transient(KeyError("missing"))

    def test_http_transport_and_retryable_statuses(self):
        request = httpx.Request("POST", "https://provider.invalid/v1/chat")
        unavailable = httpx.Response(503, request=request)
        unauthorized = httpx.Response(401, request=request)

        assert is_transient(httpx.ConnectError("connection failed", request=request))
        assert is_transient(
            httpx.HTTPStatusError(
                "service unavailable", request=request, response=unavailable
            )
        )
        assert not is_transient(
            httpx.HTTPStatusError("unauthorized", request=request, response=unauthorized)
        )


class TestWithRetry:
    @staticmethod
    def fake_sleep():
        delays = []

        async def sleep(delay):
            delays.append(delay)

        return delays, sleep

    @pytest.mark.asyncio
    async def test_succeeds_first_try(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            return "ok"

        result = await with_retry(fn)
        assert result == "ok"
        assert calls == 1

    @pytest.mark.asyncio
    async def test_retries_on_transient(self):
        calls = 0
        delays, sleep = self.fake_sleep()

        async def fn():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ConnectionError("refused")
            return "ok"

        config = RetryConfig(max_retries=3, base_delay=0.01)
        result = await with_retry(fn, config=config, sleep_func=sleep)
        assert result == "ok"
        assert calls == 3
        assert delays == [0.01, 0.02]

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        delays, sleep = self.fake_sleep()

        async def fn():
            raise ConnectionError("always fails")

        config = RetryConfig(max_retries=2, base_delay=0.01)
        with pytest.raises(ConnectionError):
            await with_retry(fn, config=config, sleep_func=sleep)
        assert delays == [0.01, 0.02]

    @pytest.mark.asyncio
    async def test_no_retry_on_non_transient(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            raise ValueError("bad input")

        config = RetryConfig(max_retries=3, base_delay=0.01)
        with pytest.raises(ValueError):
            await with_retry(fn, config=config)
        assert calls == 1  # no retries

    @pytest.mark.asyncio
    async def test_on_retry_callback(self):
        retries = []
        delays, sleep = self.fake_sleep()

        async def fn():
            if len(retries) < 2:
                raise ConnectionError("fail")
            return "ok"

        async def on_retry(attempt, error, delay):
            retries.append(attempt)

        config = RetryConfig(max_retries=3, base_delay=0.01)
        result = await with_retry(fn, config=config, on_retry=on_retry, sleep_func=sleep)
        assert result == "ok"
        assert retries == [1, 2]
        assert delays == [0.01, 0.02]

    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Verify delays increase exponentially."""
        delays, sleep = self.fake_sleep()
        callback_delays = []

        async def fn():
            raise TimeoutError("slow")

        async def on_retry(attempt, error, delay):
            callback_delays.append(delay)

        config = RetryConfig(max_retries=4, base_delay=0.01, backoff_factor=2.0, max_delay=1.0)
        with pytest.raises(TimeoutError):
            await with_retry(fn, config=config, on_retry=on_retry, sleep_func=sleep)

        assert delays == [0.01, 0.02, 0.04, 0.08]
        assert callback_delays == delays

    @pytest.mark.asyncio
    async def test_delay_cap(self):
        delays, sleep = self.fake_sleep()
        callback_delays = []

        async def fn():
            raise ConnectionError("fail")

        async def on_retry(attempt, error, delay):
            callback_delays.append(delay)

        config = RetryConfig(max_retries=10, base_delay=1.0, backoff_factor=3.0, max_delay=5.0)
        with pytest.raises(ConnectionError):
            await with_retry(fn, config=config, on_retry=on_retry, sleep_func=sleep)

        assert delays == [1.0, 3.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        assert callback_delays == delays

    @pytest.mark.asyncio
    async def test_with_args_and_kwargs(self):
        async def fn(a, b, c=0):
            return a + b + c

        result = await with_retry(fn, 1, 2, c=3)
        assert result == 6
