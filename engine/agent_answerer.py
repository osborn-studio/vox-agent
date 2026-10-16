"""Pluggable answer seam for the agent-query endpoint.

Mirrors engine.asr: a Protocol + a deterministic mock (default) + an
env-selected registry (VOXAGENT_ANSWERER). The mock produces a real,
grounded, cited answer by synthesizing over the matched memory nodes — no
LLM required, fully deterministic for tests. The `llm` answerer synthesizes
a real answer via an OpenAI-compatible chat/completions endpoint (ARK /
硅基流动 / DeepSeek / OpenAI all speak it), configured via VOXAGENT_LLM_*,
behind the same seam — the endpoint and its callers are unchanged.
"""

from __future__ import annotations

import os
from typing import Callable, Protocol

_PREVIEW_CHARS = 200
_ALSO_LIMIT = 3

# Bounded per-node context handed to the LLM: title + a truncated body, capped
# to a handful of nodes. Memory nodes are already summaries (not raw
# transcript), and truncation keeps the prompt bounded regardless of node size
# — no unbounded text ever leaves here.
_LLM_CONTEXT_CHARS = 500
_LLM_MAX_NODES = 8

_LLM_SYSTEM_PROMPT = (
    "You answer questions using ONLY the provided memory context. Be concise. "
    "Cite the item numbers you used (e.g. [1], [2]). If the context does not "
    "answer the question, say so plainly rather than guessing."
)


class AnswererAdapter(Protocol):
    name: str

    def answer(self, query: str, context_nodes: list[dict]) -> str:
        """Return a natural-language answer grounded in the context nodes."""
        ...


class MockAnswerer:
    """Deterministic extractive answerer over the matched memory nodes."""

    name = "mock"

    def answer(self, query: str, context_nodes: list[dict]) -> str:
        if not context_nodes:
            return f"No memory found for “{query}”."
        lead = context_nodes[0]
        title = lead.get("title") or ""
        body = (lead.get("body") or "")[:_PREVIEW_CHARS]
        answer = f"Based on {len(context_nodes)} memory item(s) for “{query}”: {title} — {body}"
        others = [n.get("title") or "" for n in context_nodes[1:1 + _ALSO_LIMIT]]
        others = [t for t in others if t]
        if others:
            answer += f" (also: {', '.join(others)})"
        return answer


class LlmAnswerer:
    """Real LLM answerer over an OpenAI-compatible chat/completions endpoint.

    Config (all required at call time, not construction time — so
    get_answerer("llm") never fails just for being selected):
      VOXAGENT_LLM_BASE_URL   e.g. https://api.siliconflow.cn/v1
      VOXAGENT_LLM_API_KEY    bearer token
      VOXAGENT_LLM_MODEL      e.g. deepseek-chat

    `complete` is injectable for tests (takes a messages list, returns the
    assistant text); the default performs the real HTTP call via httpx.
    """

    name = "llm"

    def __init__(self, complete: Callable[[list[dict]], str] | None = None) -> None:
        self._complete = complete or self._http_complete
        self.base_url = os.environ.get("VOXAGENT_LLM_BASE_URL")
        self.api_key = os.environ.get("VOXAGENT_LLM_API_KEY")
        self.model = os.environ.get("VOXAGENT_LLM_MODEL")

    def _ensure_config(self) -> None:
        missing = [
            name
            for name, value in (
                ("VOXAGENT_LLM_BASE_URL", self.base_url),
                ("VOXAGENT_LLM_API_KEY", self.api_key),
                ("VOXAGENT_LLM_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "LLM answerer not configured — set " + ", ".join(missing)
            )

    def _http_complete(self, messages: list[dict]) -> str:
        self._ensure_config()
        import httpx

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.2,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            # Normalize upstream failures (bad key/quota/etc.) — never echo the
            # response body, which could contain sensitive detail. Status only.
            raise RuntimeError(
                f"LLM request failed with status {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"LLM request failed: {type(exc).__name__}") from exc
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM response was not in the expected format") from exc

    def _build_messages(self, query: str, context_nodes: list[dict]) -> list[dict]:
        lines = []
        for i, node in enumerate(context_nodes[:_LLM_MAX_NODES], start=1):
            title = node.get("title") or ""
            body = (node.get("body") or "")[:_LLM_CONTEXT_CHARS]
            lines.append(f"{i}. [{node.get('type')}] {title}: {body}")
        context = "\n".join(lines)
        return [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {"role": "user", "content": f"Question: {query}\n\nMemory context:\n{context}"},
        ]

    def answer(self, query: str, context_nodes: list[dict]) -> str:
        if not context_nodes:
            return f"No memory found for “{query}”."
        return (self._complete(self._build_messages(query, context_nodes)) or "").strip()


_ANSWERER_FACTORIES = {
    "mock": MockAnswerer,
    "llm": LlmAnswerer,
}


def get_answerer(name: str | None = None) -> AnswererAdapter:
    resolved = name or os.environ.get("VOXAGENT_ANSWERER", "mock")
    try:
        return _ANSWERER_FACTORIES[resolved]()
    except KeyError:
        raise ValueError(
            f"unknown answerer: {resolved!r} (available: {sorted(_ANSWERER_FACTORIES)})"
        ) from None
