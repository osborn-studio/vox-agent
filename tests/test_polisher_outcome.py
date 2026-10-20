"""polish_outcome reports ok / skipped / failed without leaking provider bodies."""

import asyncio
import json

import httpx

from runtime.llm_provider import LlmClient
from runtime.models.config import VoxAgentConfig
from runtime.slowpath.polisher import PolishOutcome, Polisher


def _config(llm_main: str = "qwen-turbo", key: str = "k") -> VoxAgentConfig:
    config = VoxAgentConfig()
    # Defaults read the environment (runtime/models/config.py:62-63, :102); pin them.
    config.dashscope_key_cn = key
    config.dashscope_key_us = ""  # the Qwen route falls back to the US key when CN is empty
    config.api_token = ""
    config.profiles[config.active_profile].llm_main = llm_main
    return config


def _client(handler) -> LlmClient:
    return LlmClient(transport=httpx.MockTransport(handler), timeout_seconds=2.0)


def test_ok_outcome_returns_polished_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["messages"][1]["content"] == "嗯 那个 测试"
        return httpx.Response(200, json={"choices": [{"message": {"content": "测试"}}]})

    outcome = asyncio.run(Polisher(client=_client(handler)).polish_outcome("嗯 那个 测试", _config()))
    assert outcome == PolishOutcome(status="ok", text="测试", error=None)


def test_skipped_when_provider_not_configured():
    outcome = asyncio.run(
        Polisher(client=_client(lambda r: httpx.Response(500))).polish_outcome("x", _config(key=""))
    )
    assert outcome.status == "skipped"
    assert outcome.text is None
    assert outcome.error == "qwen-turbo: not configured"


def test_skipped_on_blank_input():
    outcome = asyncio.run(
        Polisher(client=_client(lambda r: httpx.Response(500))).polish_outcome("   ", _config())
    )
    assert outcome == PolishOutcome(status="skipped", text=None, error="empty input")


def test_failed_carries_only_provider_label_and_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "SECRET-BODY sk-live-123"}})

    outcome = asyncio.run(Polisher(client=_client(handler)).polish_outcome("测试", _config()))
    assert outcome.status == "failed"
    assert outcome.text is None
    assert "429" in outcome.error
    assert "SECRET" not in outcome.error and "sk-live" not in outcome.error


def test_failed_on_total_deadline():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        return httpx.Response(200, json={"choices": [{"message": {"content": "late"}}]})

    polisher = Polisher(client=_client(slow), deadline_seconds=0.05)
    outcome = asyncio.run(polisher.polish_outcome("测试", _config()))
    assert outcome.status == "failed"
    assert outcome.error == "Qwen Bailian: timed out"


def test_legacy_polish_contract_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    assert asyncio.run(Polisher(client=_client(handler)).polish("测试", _config())) is None
