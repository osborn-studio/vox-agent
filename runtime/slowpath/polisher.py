"""LLM polisher — slow-path default, with an explicit synchronous outcome for opt-in inline use."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

import structlog

from runtime.llm_provider import LlmClient, LlmProviderError, resolve_llm_provider
from runtime.models.config import VoxLogConfig

logger = structlog.get_logger()

POLISH_PROMPT = """你是一个语音转文字的后处理助手。清理 ASR 的原始输出。

规则：
1. 去掉口头禅（嗯、啊、那个、就是说、um、uh）
2. 修正明显的语音识别错误
3. 保持原意不变，不添加内容
4. 中英文之间加空格
5. 修正标点符号
6. 保持技术术语原始拼写
7. 只输出清理后的文字"""

PolishStatus = Literal["ok", "skipped", "failed"]


@dataclass(frozen=True)
class PolishOutcome:
    """Result of one synchronous polish attempt.

    `error` carries only a provider label and a failure class (HTTP status,
    timeout, not configured); never a response body or a credential.
    """

    status: PolishStatus
    text: str | None
    error: str | None


class Polisher:
    def __init__(self, *, client: LlmClient | None = None, deadline_seconds: float = 15.0) -> None:
        self._client = client or LlmClient(timeout_seconds=15.0)
        self._deadline_seconds = deadline_seconds

    async def polish_outcome(self, text: str, config: VoxLogConfig) -> PolishOutcome:
        if not text or not text.strip():
            return PolishOutcome("skipped", None, "empty input")

        llm_name = config.profile.llm_main
        provider = resolve_llm_provider(config, llm_name)
        if not provider:
            logger.debug("polisher.skip", provider=llm_name, reason="provider_not_configured")
            return PolishOutcome("skipped", None, f"{llm_name}: not configured")

        try:
            polished = await asyncio.wait_for(
                self._client.complete(
                    provider,
                    system_prompt=POLISH_PROMPT,
                    user_text=text,
                    max_tokens=2_000,
                ),
                timeout=self._deadline_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("polisher.fail", provider=provider.id, error="deadline")
            return PolishOutcome("failed", None, f"{provider.label}: timed out")
        except LlmProviderError as exc:
            logger.warning("polisher.fail", provider=provider.id, error=str(exc)[:100])
            return PolishOutcome("failed", None, _bounded_error(provider.label, str(exc)))
        except Exception as exc:  # noqa: BLE001 - never raise into the request path
            logger.warning("polisher.fail", provider=provider.id, error=type(exc).__name__)
            return PolishOutcome("failed", None, f"{provider.label}: {type(exc).__name__}")
        return PolishOutcome("ok", polished, None)

    async def polish(self, text: str, config: VoxLogConfig) -> str | None:
        """Legacy slow-path contract: polished text on success, otherwise None."""
        outcome = await self.polish_outcome(text, config)
        return outcome.text if outcome.status == "ok" else None


_STATUS = re.compile(r"\((\d{3})\)")


def _bounded_error(label: str, message: str) -> str:
    """Keep the provider label and an HTTP status if the client reported one."""
    match = _STATUS.search(message)
    if match:
        return f"{label}: HTTP {match.group(1)}"
    if "timed out" in message:
        return f"{label}: timed out"
    return f"{label}: request failed"
