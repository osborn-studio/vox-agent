"""LLM polisher — runs in slow path only. Never blocks fast path."""

from __future__ import annotations

import structlog

from runtime.llm_provider import LlmClient, resolve_llm_provider
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


class Polisher:
    def __init__(self, *, client: LlmClient | None = None) -> None:
        self._client = client or LlmClient(timeout_seconds=15.0)

    async def polish(self, text: str, config: VoxLogConfig) -> str | None:
        if not text or not text.strip():
            return None

        llm_name = config.profile.llm_main
        provider = resolve_llm_provider(config, llm_name)
        if not provider:
            logger.debug("polisher.skip", provider=llm_name, reason="provider_not_configured")
            return None

        try:
            return await self._client.complete(
                provider,
                system_prompt=POLISH_PROMPT,
                user_text=text,
                max_tokens=2_000,
            )
        except Exception as e:
            logger.warning("polisher.fail", provider=provider.id, error=str(e)[:100])

        return None
