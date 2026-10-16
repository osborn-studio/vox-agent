"""Tests for the minimal AI Mate Memory digest export sink."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.ai_mate_memory import AIMateMemorySink


def test_ai_mate_memory_sink_exports_project_digest(tmp_path: Path):
    sink = AIMateMemorySink(tmp_path)
    result = sink.export_digest(
        scope="project",
        markdown="# project_digest\n\nCursor work",
        project_key="cursor",
    )
    assert result.bytes_written > 0
    payload = json.loads(Path(result.record_path).read_text(encoding="utf-8"))
    assert payload["scope"] == "project"
    assert payload["project_key"] == "cursor"
    assert "Cursor work" in payload["content_markdown"]


@pytest.mark.asyncio
async def test_desktop_api_returns_complete_ai_mate_contract(tmp_path: Path, monkeypatch):
    from apps.desktop import server

    async def fake_export(*_args, **_kwargs):
        return "# project_digest\n\nCursor work"

    class FakeRequest:
        async def json(self):
            return {
                "scope": "project",
                "project_key": "cursor",
                "base_dir": str(tmp_path),
            }

    monkeypatch.setattr(server, "_export_digest_markdown", fake_export)
    response = await server.export_ai_mate_memory_endpoint(FakeRequest(), None)

    assert response["ok"] is True
    assert response["bytes_written"] > 0
