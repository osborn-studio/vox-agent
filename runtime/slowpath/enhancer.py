"""Optional LLM enhancer for digest generation."""

from __future__ import annotations

import json

import structlog

from runtime.llm_provider import LlmClient, LlmProvider, resolve_llm_provider
from runtime.models.config import VoxLogConfig

logger = structlog.get_logger()

ENHANCE_PROMPT = """You are a structured memory compiler for a voice workflow.

Return strict JSON with keys:
- summary: concise sentence under 220 chars
- intent: one of coding, planning, debugging, review, general
- suggested_tags: array of short tags
- mentioned_entities: array of entity strings

Keep the output faithful to the source text. Do not invent facts."""


class DigestEnhancer:
    def __init__(self, *, client: LlmClient | None = None) -> None:
        self._client = client or LlmClient(timeout_seconds=20.0)

    async def enhance(self, digest: dict, source_text: str, config: VoxLogConfig) -> dict:
        payload = dict(digest)
        if not config.digest_enhancement_enabled:
            payload["enhanced"] = False
            payload["enhancer_provider"] = "heuristic"
            return payload
        provider = self._resolve_provider(config)
        if not provider:
            payload["enhanced"] = False
            payload["enhancer_provider"] = "heuristic"
            return payload

        try:
            completion = await self._client.complete(
                provider,
                system_prompt=ENHANCE_PROMPT,
                user_text=source_text,
                max_tokens=400,
            )
            result = _parse_enhancement(completion)

            payload["summary"] = result.get("summary") or payload["summary"]
            payload["intent"] = result.get("intent") or payload["intent"]
            payload["suggested_tags"] = result.get("suggested_tags") or payload["suggested_tags"]
            payload["mentioned_entities"] = result.get("mentioned_entities") or payload["mentioned_entities"]
            payload["enhanced"] = True
            payload["enhancer_provider"] = provider.id
            return payload
        except Exception as exc:
            logger.warning("digest.enhancer_fail", provider=provider.id, error=str(exc)[:160])
            payload["enhanced"] = False
            payload["enhancer_provider"] = "heuristic"
            return payload

    def _resolve_provider(self, config: VoxLogConfig) -> LlmProvider | None:
        preferred = (config.digest_enhancement_provider or "auto").lower()
        route = config.profile.llm_main if preferred in {"", "auto"} else preferred
        aliases = {"openai": "openai-gpt", "qwen": "qwen-turbo", "custom": "openai-compatible-custom"}
        return resolve_llm_provider(config, aliases.get(route, route))


def _parse_enhancement(value: str) -> dict:
    payload = json.loads(value)
    if not isinstance(payload, dict) or set(payload) != {
        "summary",
        "intent",
        "suggested_tags",
        "mentioned_entities",
    }:
        raise ValueError("digest enhancement response has an invalid shape")
    if (
        not isinstance(payload["summary"], str)
        or not payload["summary"].strip()
        or len(payload["summary"]) > 220
        or payload["intent"] not in {"coding", "planning", "debugging", "review", "general"}
        or not _short_string_list(payload["suggested_tags"])
        or not _short_string_list(payload["mentioned_entities"])
    ):
        raise ValueError("digest enhancement response exceeds its contract")
    return payload


def _short_string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= 20
        and all(isinstance(item, str) and 0 < len(item.strip()) <= 100 for item in value)
    )
