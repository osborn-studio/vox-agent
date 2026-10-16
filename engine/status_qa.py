"""Minimal permissioned status Q&A without LLM calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .org_models import RequestContext


class StatusRetriever(Protocol):
    def search(
        self, ctx: RequestContext, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class StatusAnswer:
    answer: str
    confidence: float
    confident: bool
    source_ids: list[str]
    citations: list[dict[str, str]]


def answer_status_question(
    retriever: StatusRetriever,
    ctx: RequestContext,
    question: str,
    limit: int = 5,
) -> StatusAnswer:
    """Answer status questions using only permission-filtered retrieved sources."""

    sources = _visible_sources_with_ids(retriever.search(ctx, question, limit=limit))
    source_ids = [source["source_id"] for source in sources]
    citations = [{"source_id": source_id} for source_id in source_ids]

    if not citations:
        return StatusAnswer(
            answer="缺少可见上下文，无法基于可引用来源回答这个状态问题。",
            confidence=0.2,
            confident=False,
            source_ids=[],
            citations=[],
        )

    lines = [
        f"- {source['body'].strip()} [{source['source_id']}]"
        for source in sources
        if source.get("body", "").strip()
    ]
    if not lines:
        return StatusAnswer(
            answer="缺少可见上下文，无法基于可引用来源回答这个状态问题。",
            confidence=0.2,
            confident=False,
            source_ids=[],
            citations=[],
        )

    return StatusAnswer(
        answer="基于可见来源的状态回答：\n" + "\n".join(lines),
        confidence=0.65,
        confident=True,
        source_ids=source_ids,
        citations=citations,
    )


def _visible_sources_with_ids(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible_sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        source_id = str(source.get("source_id") or "").strip()
        body = str(source.get("body") or "").strip()
        if not source_id or not body or source_id in seen:
            continue
        seen.add(source_id)
        visible_sources.append({**source, "source_id": source_id, "body": body})
    return visible_sources
