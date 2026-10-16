"""Web tools — web_search and web_fetch."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from . import ToolDefinition, ToolResult

if TYPE_CHECKING:
    from . import ToolRegistry


async def _web_fetch(params: dict[str, Any]) -> ToolResult:
    url = params.get("url", "")
    if not url:
        return ToolResult(error="url required", success=False)
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "VoxAgents/0.1"})
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            text = resp.text
            # strip HTML tags for readability if HTML
            if "html" in content_type:
                text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
                text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            # truncate to reasonable size
            max_len = params.get("max_length", 8000)
            if len(text) > max_len:
                text = text[:max_len] + f"\n... (truncated, {len(resp.text)} total chars)"
            return ToolResult(output=text)
    except Exception as e:
        return ToolResult(error=str(e), success=False)


async def _web_search(params: dict[str, Any]) -> ToolResult:
    query = params.get("query", "")
    if not query:
        return ToolResult(error="query required", success=False)
    # Use DuckDuckGo HTML (no API key needed)
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "VoxAgents/0.1"},
            )
            resp.raise_for_status()
            # Extract result snippets
            results = []
            for match in re.finditer(
                r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
                r'class="result__snippet"[^>]*>(.*?)</span>',
                resp.text,
                re.DOTALL,
            ):
                url = match.group(1)
                title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
                snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()
                if title and snippet:
                    results.append(f"[{title}]({url})\n{snippet}")
                if len(results) >= 5:
                    break
            if not results:
                return ToolResult(output=f"No results found for: {query}")
            return ToolResult(output="\n\n".join(results))
    except Exception as e:
        return ToolResult(error=str(e), success=False)


def register_web_tools(registry: "ToolRegistry") -> None:
    registry.register(ToolDefinition(
        name="web_fetch",
        description="Fetch a URL and return its text content",
        parameters={"url": "string", "max_length": "int?"},
        handler=_web_fetch,
        requires_permission=False,
    ))
    registry.register(ToolDefinition(
        name="web_search",
        description="Search the web using DuckDuckGo",
        parameters={"query": "string"},
        handler=_web_search,
        requires_permission=False,
    ))
