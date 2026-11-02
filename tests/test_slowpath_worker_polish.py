"""The slow-path worker never re-polishes an event that was polished inline."""

from __future__ import annotations

import asyncio

from runtime.models.config import VoxAgentConfig
from runtime.models.events import (
    ArchiveStatus,
    ExportStatus,
    OutputMode,
    RecordingMode,
    TargetRiskLevel,
    VoiceEvent,
)
from runtime.slowpath import worker


class FakeArchive:
    def __init__(self) -> None:
        self.saved: list[str] = []
        self.polish_updates: list[tuple[str, str]] = []

    async def save_event(self, event: VoiceEvent) -> None:
        self.saved.append(event.id)

    async def update_polish(self, event_id: str, polished: str) -> None:
        self.polish_updates.append((event_id, polished))

    async def upsert_session_digest(self, **_kwargs) -> None:
        return None

    async def list_events_for_date(self, _date: str) -> list[VoiceEvent]:
        return []

    async def upsert_daily_digest(self, **_kwargs) -> None:
        return None

    async def list_events_for_project(self, _key: str) -> list[VoiceEvent]:
        return []

    async def upsert_project_digest(self, **_kwargs) -> None:
        return None


class CountingPolisher:
    def __init__(self, result: str | None = "润色") -> None:
        self.calls: list[str] = []
        self.result = result

    async def polish(self, text: str, config) -> str | None:
        self.calls.append(text)
        return self.result


def _event(polished_text: str) -> VoiceEvent:
    return VoiceEvent(
        source="desktop",
        env="home",
        agent="",
        target_app="",
        target_risk_level=TargetRiskLevel.LOW,
        raw_text="嗯 原文",
        display_text="嗯 原文",
        polished_text=polished_text,
        audio_duration_ms=10,
        stt_provider="fake",
        stt_model="fake",
        latency_stt_ms=1,
        latency_total_fast_ms=1,
        recording_mode=RecordingMode.NORMAL,
        output_mode=OutputMode.PASTE,
        confidence=0.9,
        archive_status=ArchiveStatus.QUEUED,
        export_status=ExportStatus.PENDING,
        session_id="",
        role="me",
    )


def _config() -> VoxAgentConfig:
    config = VoxAgentConfig()
    config.dashscope_key_cn = ""
    config.dashscope_key_us = ""
    config.openai_key = ""
    return config


def test_worker_persists_inline_polish_without_calling_the_provider():
    archive = FakeArchive()
    polisher = CountingPolisher()
    event = _event(polished_text="润色")

    asyncio.run(worker._process_event(event, _config(), archive, polisher))

    assert polisher.calls == []
    assert archive.polish_updates == [(event.id, "润色")]
    assert event.archive_status == ArchiveStatus.POLISHED


def test_worker_still_polishes_events_without_inline_result():
    archive = FakeArchive()
    polisher = CountingPolisher()
    event = _event(polished_text="")

    asyncio.run(worker._process_event(event, _config(), archive, polisher))

    assert polisher.calls == ["嗯 原文"]
    assert archive.polish_updates == [(event.id, "润色")]
    assert event.polished_text == "润色"
    assert event.archive_status == ArchiveStatus.POLISHED
