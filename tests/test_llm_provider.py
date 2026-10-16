from __future__ import annotations

import json

import httpx
import pytest

from runtime.llm_provider import LlmClient, LlmProviderError, resolve_llm_provider
from runtime.models.config import VoxLogConfig


def _config() -> VoxLogConfig:
    return VoxLogConfig(
        openai_key="openai-test",
        dashscope_key_cn="qwen-cn-test",
        dashscope_key_us="qwen-us-test",
        doubao_key="doubao-test",
        anthropic_key="anthropic-test",
        custom_llm_key="custom-test",
        custom_llm_base_url="https://llm.example.test/v1",
        custom_llm_model="team-model",
    )


def test_resolver_consolidates_windows_llm_routes_without_exposing_secrets() -> None:
    config = _config()

    openai = resolve_llm_provider(config, "openai-gpt")
    qwen = resolve_llm_provider(config, "qwen-turbo")
    doubao = resolve_llm_provider(config, "doubao")
    claude = resolve_llm_provider(config, "claude")
    custom = resolve_llm_provider(config, "openai-compatible-custom")
    ollama = resolve_llm_provider(config, "ollama")

    assert openai and openai.protocol == "openai_compatible"
    assert openai.model == "gpt-5-mini"
    assert qwen and qwen.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert qwen.model == "qwen3.7-plus"
    assert doubao and doubao.model == config.doubao_model
    assert claude and claude.protocol == "anthropic"
    assert custom and custom.model == "team-model"
    assert ollama and ollama.requires_api_key is False
    assert "openai-test" not in repr(openai)
    assert resolve_llm_provider(VoxLogConfig(openai_key=""), "openai-gpt") is None

    us_only = _config()
    us_only.dashscope_key_cn = ""
    qwen_us = resolve_llm_provider(us_only, "qwen-turbo")
    assert qwen_us and qwen_us.base_url == "https://dashscope-us.aliyuncs.com/compatible-mode/v1"


@pytest.mark.asyncio
async def test_openai_gpt5_omits_unsupported_sampling_parameter() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})

    provider = resolve_llm_provider(_config(), "openai-gpt")
    assert provider
    client = LlmClient(transport=httpx.MockTransport(respond))

    await client.complete(
        provider,
        system_prompt="Preserve meaning.",
        user_text="raw text",
        max_tokens=200,
    )

    body = json.loads(requests[0].content)
    assert "temperature" not in body


@pytest.mark.asyncio
async def test_openai_compatible_client_uses_one_bounded_chat_contract() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": " polished text "}}]},
        )

    provider = resolve_llm_provider(_config(), "doubao")
    assert provider
    client = LlmClient(transport=httpx.MockTransport(respond))

    result = await client.complete(
        provider,
        system_prompt="Preserve meaning.",
        user_text="raw text",
        max_tokens=200,
    )

    assert result == "polished text"
    assert len(requests) == 1
    assert requests[0].url == f"{provider.base_url}/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer doubao-test"
    body = json.loads(requests[0].content)
    assert body["model"] == provider.model
    assert body["messages"][1] == {"role": "user", "content": "raw text"}
    assert body["max_tokens"] == 200


@pytest.mark.asyncio
async def test_anthropic_client_uses_messages_contract_and_safe_errors() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "secret response"}})

    provider = resolve_llm_provider(_config(), "claude")
    assert provider
    client = LlmClient(transport=httpx.MockTransport(respond))

    with pytest.raises(LlmProviderError, match=r"rejected the request \(401\)") as caught:
        await client.complete(
            provider,
            system_prompt="Preserve meaning.",
            user_text="raw text",
            max_tokens=200,
        )

    assert "secret response" not in str(caught.value)
    assert requests[0].url == f"{provider.base_url}/messages"
    assert requests[0].headers["x-api-key"] == "anthropic-test"
    assert requests[0].headers["anthropic-version"] == "2023-06-01"
    body = json.loads(requests[0].content)
    assert body["model"] == "claude-sonnet-5"
    assert "temperature" not in body


@pytest.mark.asyncio
async def test_client_rejects_an_empty_completion() -> None:
    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]})

    provider = resolve_llm_provider(_config(), "openai-gpt")
    assert provider
    client = LlmClient(transport=httpx.MockTransport(respond))

    with pytest.raises(LlmProviderError, match="invalid completion"):
        await client.complete(
            provider,
            system_prompt="Preserve meaning.",
            user_text="raw text",
            max_tokens=200,
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///tmp/model",
        "http://llm.example.test/v1",
        "https://user:password@example.test/v1",
        "https://example.test/v1?api_key=secret",
    ],
)
def test_custom_provider_rejects_unsafe_base_urls(base_url: str) -> None:
    config = _config()
    config.custom_llm_base_url = base_url

    assert resolve_llm_provider(config, "openai-compatible-custom") is None
