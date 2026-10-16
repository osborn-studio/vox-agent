"""Shared, secret-safe LLM provider routing for the Vox Agent runtime."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

import httpx

from runtime.models.config import VoxLogConfig

LlmProtocol = Literal["openai_compatible", "anthropic"]


class LlmProviderError(RuntimeError):
    """A bounded provider failure that never includes response or credential material."""

    def __init__(self, message: str, *, retriable: bool) -> None:
        super().__init__(message)
        self.retriable = retriable


@dataclass(frozen=True)
class LlmProvider:
    id: str
    label: str
    protocol: LlmProtocol
    base_url: str
    model: str
    api_key: str = field(repr=False)
    requires_api_key: bool = True
    local: bool = False


def normalize_llm_base_url(value: str, *, allow_empty: bool = False) -> str | None:
    candidate = value.strip().rstrip("/")
    if not candidate:
        return "" if allow_empty else None
    if len(candidate) > 500:
        return None
    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        return None
    return candidate


def _is_loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def normalize_llm_model(value: str, *, allow_empty: bool = False) -> str | None:
    model = value.strip()
    if not model:
        return "" if allow_empty else None
    if len(model) > 200 or any(char in model for char in "\r\n\0"):
        return None
    return model


def _provider(
    *,
    provider_id: str,
    label: str,
    protocol: LlmProtocol,
    base_url: str,
    model: str,
    api_key: str,
    requires_api_key: bool = True,
    local: bool = False,
) -> LlmProvider | None:
    safe_url = normalize_llm_base_url(base_url)
    safe_model = normalize_llm_model(model)
    secret = api_key.strip()
    if not safe_url or not safe_model or (requires_api_key and not secret):
        return None
    return LlmProvider(
        id=provider_id,
        label=label,
        protocol=protocol,
        base_url=safe_url,
        model=safe_model,
        api_key=secret,
        requires_api_key=requires_api_key,
        local=local,
    )


def resolve_llm_provider(config: VoxLogConfig, route: str) -> LlmProvider | None:
    """Resolve an allowlisted route without contacting a provider."""

    normalized = route.strip().lower()
    if normalized in {"openai", "openai-gpt", "gpt"}:
        return _provider(
            provider_id="openai-gpt",
            label="OpenAI GPT",
            protocol="openai_compatible",
            base_url="https://api.openai.com/v1",
            model=config.openai_llm_model,
            api_key=config.openai_key,
        )
    if normalized in {"qwen", "qwen-turbo", "bailian", "bailian-cn", "bailian-us"}:
        use_cn = normalized != "bailian-us" and bool(config.dashscope_key_cn)
        key = config.dashscope_key_cn if use_cn else config.dashscope_key_us
        base_url = (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
            if use_cn
            else "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
        )
        return _provider(
            provider_id="qwen-turbo",
            label="Qwen Bailian",
            protocol="openai_compatible",
            base_url=base_url,
            model=config.qwen_llm_model,
            api_key=key,
        )
    if normalized in {"doubao", "doubao-ark", "ark"}:
        return _provider(
            provider_id="doubao",
            label="Doubao ARK",
            protocol="openai_compatible",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            model=config.doubao_model,
            api_key=config.doubao_key,
        )
    if normalized in {"claude", "anthropic"}:
        return _provider(
            provider_id="claude",
            label="Claude",
            protocol="anthropic",
            base_url="https://api.anthropic.com/v1",
            model=config.anthropic_model,
            api_key=config.anthropic_key,
        )
    if normalized in {"openai-compatible-custom", "custom", "custom-openai"}:
        return _provider(
            provider_id="openai-compatible-custom",
            label="Custom OpenAI-compatible",
            protocol="openai_compatible",
            base_url=config.custom_llm_base_url,
            model=config.custom_llm_model,
            api_key=config.custom_llm_key,
            requires_api_key=False,
        )
    if normalized in {"ollama", "ollama-local"}:
        return _provider(
            provider_id="ollama",
            label="Ollama Local",
            protocol="openai_compatible",
            base_url="http://127.0.0.1:11434/v1",
            model=config.ollama_model,
            api_key="",
            requires_api_key=False,
            local=True,
        )
    return None


class LlmClient:
    """One bounded client for OpenAI-compatible and Anthropic message protocols."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def complete(
        self,
        provider: LlmProvider,
        *,
        system_prompt: str,
        user_text: str,
        max_tokens: int,
        temperature: float = 0.1,
    ) -> str:
        if (
            not system_prompt.strip()
            or len(system_prompt) > 16_000
            or not user_text.strip()
            or len(user_text) > 64_000
            or isinstance(max_tokens, bool)
            or not 1 <= max_tokens <= 8_192
            or not 0 <= temperature <= 2
        ):
            raise ValueError("LLM completion input is invalid or exceeds its limit")

        if provider.protocol == "anthropic":
            path = "/messages"
            headers = {
                "content-type": "application/json",
                "x-api-key": provider.api_key,
                "anthropic-version": "2023-06-01",
            }
            body = {
                "model": provider.model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_text}],
                "max_tokens": max_tokens,
            }
        else:
            path = "/chat/completions"
            headers = {"content-type": "application/json"}
            if provider.api_key:
                headers["Authorization"] = f"Bearer {provider.api_key}"
            body = {
                "model": provider.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                "max_tokens": max_tokens,
            }
            if not (provider.id == "openai-gpt" and provider.model.startswith("gpt-5")):
                body["temperature"] = temperature

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(f"{provider.base_url}{path}", headers=headers, json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise LlmProviderError(
                f"{provider.label} timed out.",
                retriable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise LlmProviderError(
                f"{provider.label} rejected the request ({status}).",
                retriable=status == 429 or status >= 500,
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LlmProviderError(
                f"{provider.label} returned an unusable response.",
                retriable=True,
            ) from exc

        content = self._content(provider, payload)
        normalized = content.strip()
        if not normalized or len(normalized) > 64_000:
            raise LlmProviderError(
                f"{provider.label} returned an invalid completion.",
                retriable=True,
            )
        return normalized

    @staticmethod
    def _content(provider: LlmProvider, payload: object) -> str:
        try:
            if provider.protocol == "anthropic":
                blocks = payload["content"]  # type: ignore[index]
                return "".join(
                    block["text"]
                    for block in blocks
                    if isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                )
            content = payload["choices"][0]["message"]["content"]  # type: ignore[index]
            return content if isinstance(content, str) else ""
        except (KeyError, IndexError, TypeError):
            return ""
