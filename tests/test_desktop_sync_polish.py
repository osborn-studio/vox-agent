"""Inline polish is opt-in, explicit in the response, and never sends silently."""

import pytest
from fastapi.testclient import TestClient

from apps.desktop import server
from runtime.models.config import VoxAgentConfig
from runtime.slowpath.polisher import PolishOutcome


class FakePolisher:
    def __init__(self, outcome: PolishOutcome) -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    async def polish_outcome(self, text: str, config) -> PolishOutcome:
        self.calls.append(text)
        return self.outcome


class FakeCorrector:
    def correct_with_trace(self, text: str):
        return text, []


class FakeRecent:
    def put(self, *args, **kwargs) -> None:
        return None


@pytest.fixture
def client(monkeypatch):
    config = VoxAgentConfig()
    config.api_token = ""  # the default reads the environment; keep verify_token open in tests
    monkeypatch.setattr(server, "_config", config)
    monkeypatch.setattr(server, "_corrector", FakeCorrector())
    monkeypatch.setattr(server, "_store", object())
    monkeypatch.setattr(server, "_recent", FakeRecent())
    monkeypatch.setattr(server, "enqueue", lambda event: None)
    return TestClient(server.app)


def test_text_endpoint_without_flag_reports_skipped(client, monkeypatch):
    fake = FakePolisher(PolishOutcome("ok", "SHOULD NOT BE USED", None))
    monkeypatch.setattr(server, "_polisher", fake)
    body = client.post("/v1/voice/text", json={"text": "嗯 测试"}).json()
    assert body["polish_status"] == "skipped"
    assert body["polished_text"] == body["display_text"] == "嗯 测试"
    assert "polish_error" not in body
    assert fake.calls == []


def test_text_endpoint_with_flag_returns_polished(client, monkeypatch):
    fake = FakePolisher(PolishOutcome("ok", "测试", None))
    monkeypatch.setattr(server, "_polisher", fake)
    body = client.post("/v1/voice/text", json={"text": "嗯 测试", "polish": True}).json()
    assert body["polish_status"] == "ok"
    assert body["polished_text"] == "测试"
    assert body["display_text"] == "嗯 测试"
    assert fake.calls == ["嗯 测试"]


def test_text_endpoint_failure_keeps_display_text_and_reports_error(client, monkeypatch):
    monkeypatch.setattr(
        server, "_polisher", FakePolisher(PolishOutcome("failed", None, "Qwen Bailian: HTTP 429"))
    )
    body = client.post("/v1/voice/text", json={"text": "嗯 测试", "polish": True}).json()
    assert body["polish_status"] == "failed"
    assert body["polished_text"] == "嗯 测试"
    assert body["polish_error"] == "Qwen Bailian: HTTP 429"


def test_text_endpoint_accepts_string_flag_values(client, monkeypatch):
    fake = FakePolisher(PolishOutcome("ok", "测试", None))
    monkeypatch.setattr(server, "_polisher", fake)
    assert client.post("/v1/voice/text", json={"text": "嗯 测试", "polish": "1"}).json()["polish_status"] == "ok"
    assert client.post("/v1/voice/text", json={"text": "嗯 测试", "polish": "0"}).json()["polish_status"] == "skipped"
    assert client.post("/v1/voice/text", json={"text": "嗯 测试", "polish": False}).json()["polish_status"] == "skipped"
    assert fake.calls == ["嗯 测试"]


def test_polish_endpoint_ok(client, monkeypatch):
    monkeypatch.setattr(server, "_polisher", FakePolisher(PolishOutcome("ok", "测试", None)))
    response = client.post("/v1/text/polish", json={"text": "嗯 测试"})
    assert response.status_code == 200
    assert response.json() == {"polish_status": "ok", "polished_text": "测试"}


def test_polish_endpoint_failed_and_skipped(client, monkeypatch):
    monkeypatch.setattr(
        server, "_polisher", FakePolisher(PolishOutcome("failed", None, "Qwen Bailian: timed out"))
    )
    assert client.post("/v1/text/polish", json={"text": "x"}).json() == {
        "polish_status": "failed",
        "polished_text": "x",
        "polish_error": "Qwen Bailian: timed out",
    }
    monkeypatch.setattr(
        server, "_polisher", FakePolisher(PolishOutcome("skipped", None, "qwen-turbo: not configured"))
    )
    assert client.post("/v1/text/polish", json={"text": "x"}).json() == {
        "polish_status": "skipped",
        "polished_text": "x",
        "polish_error": "qwen-turbo: not configured",
    }


def test_polish_endpoint_without_polisher_reports_skipped(client, monkeypatch):
    monkeypatch.setattr(server, "_polisher", None)
    assert client.post("/v1/text/polish", json={"text": "x"}).json() == {
        "polish_status": "skipped",
        "polished_text": "x",
        "polish_error": "polisher unavailable",
    }


def test_polish_endpoint_rejects_empty_and_oversized(client, monkeypatch):
    fake = FakePolisher(PolishOutcome("ok", "x", None))
    monkeypatch.setattr(server, "_polisher", fake)
    assert client.post("/v1/text/polish", json={"text": "  "}).status_code == 422
    assert client.post("/v1/text/polish", json={"text": "a" * 8193}).status_code == 413
    assert client.post("/v1/text/polish", json=["not", "an", "object"]).status_code == 422
    # Non-string drafts are rejected, never stringified into content ("None", "True", "[...]").
    for bad in (None, True, 1, ["x"], {"nested": "x"}):
        assert client.post("/v1/text/polish", json={"text": bad}).status_code == 422, repr(bad)
    assert fake.calls == []


def test_polish_endpoint_requires_token_when_configured(client, monkeypatch):
    server._config.api_token = "secret"
    monkeypatch.setattr(server, "_polisher", FakePolisher(PolishOutcome("ok", "x", None)))
    assert client.post("/v1/text/polish", json={"text": "x"}).status_code == 401
    ok = client.post("/v1/text/polish", json={"text": "x"}, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


def test_apply_polish_helper_sets_every_field_explicitly():
    ok = server._apply_polish({"display_text": "原文", "polished_text": "原文"}, PolishOutcome("ok", "润色", None))
    assert ok["polished_text"] == "润色" and ok["polish_status"] == "ok" and "polish_error" not in ok
    skipped = server._apply_polish({"display_text": "原文", "polished_text": "原文"}, None)
    assert skipped["polished_text"] == "原文" and skipped["polish_status"] == "skipped" and "polish_error" not in skipped
    # Stale fields from an earlier outcome are cleared, never carried over.
    stale = {"display_text": "原文", "polished_text": "旧", "polish_status": "ok", "polish_error": "x"}
    failed = server._apply_polish(stale, PolishOutcome("failed", None, "Qwen Bailian: HTTP 500"))
    assert failed["polished_text"] == "原文"
    assert failed["polish_status"] == "failed"
    assert failed["polish_error"] == "Qwen Bailian: HTTP 500"


def test_inline_polish_result_reaches_the_slow_path_event(client, monkeypatch):
    captured = []
    monkeypatch.setattr(server, "enqueue", lambda event: captured.append(event))
    monkeypatch.setattr(server, "_polisher", FakePolisher(PolishOutcome("ok", "测试", None)))
    client.post("/v1/voice/text", json={"text": "嗯 测试", "polish": True})
    assert captured and captured[0].polished_text == "测试"


def test_failed_inline_polish_leaves_event_unpolished_for_the_slow_path(client, monkeypatch):
    captured = []
    monkeypatch.setattr(server, "enqueue", lambda event: captured.append(event))
    monkeypatch.setattr(
        server, "_polisher", FakePolisher(PolishOutcome("failed", None, "Qwen Bailian: HTTP 500"))
    )
    client.post("/v1/voice/text", json={"text": "嗯 测试", "polish": True})
    assert captured and captured[0].polished_text == ""


def _fake_fast_path_result(text: str):
    from runtime.models.events import (
        ArchiveStatus,
        ExportStatus,
        FastPathResult,
        FastPathStatus,
        OutputMode,
        RecordingMode,
        TargetRiskLevel,
        VoiceEvent,
    )

    result = FastPathResult(
        id="",
        status=FastPathStatus.OK,
        display_text=text,
        raw_text=text,
        stt_provider="fake-stt",
        stt_model="fake",
        target_app="cockpit",
        target_risk_level=TargetRiskLevel.LOW,
        should_autopaste=True,
        needs_review=False,
        confidence=0.9,
        dictionary_applied=[],
        latency_ms=1,
    )
    event = VoiceEvent(
        source="desktop",
        env="home",
        agent="",
        target_app="cockpit",
        target_risk_level=TargetRiskLevel.LOW,
        raw_text=text,
        display_text=text,
        polished_text="",
        audio_duration_ms=10,
        stt_provider="fake-stt",
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
    result.id = event.id
    return result, event


def _post_voice(client, polish: str | None):
    data = {"source": "desktop-tauri", "mode": "normal"}
    if polish is not None:
        data["polish"] = polish
    return client.post(
        "/v1/voice",
        files={"audio": ("utterance.bin", b"\x00" * 256, "application/octet-stream")},
        data=data,
    )


def test_voice_endpoint_without_flag_is_unchanged(client, monkeypatch):
    captured = []
    monkeypatch.setattr(server, "enqueue", lambda event: captured.append(event))
    fake = FakePolisher(PolishOutcome("ok", "SHOULD NOT BE USED", None))
    monkeypatch.setattr(server, "_polisher", fake)

    async def fake_fast_path(audio_bytes, config, corrector, **kwargs):
        return _fake_fast_path_result("嗯 测试")

    monkeypatch.setattr(server, "fast_path", fake_fast_path)
    body = _post_voice(client, None).json()
    assert body["polish_status"] == "skipped"
    assert body["polished_text"] == body["display_text"] == "嗯 测试"
    assert "polish_error" not in body
    assert fake.calls == []
    assert captured[0].polished_text == ""


def test_voice_endpoint_with_flag_polishes_before_enqueue(client, monkeypatch):
    captured = []
    monkeypatch.setattr(server, "enqueue", lambda event: captured.append(event))
    fake = FakePolisher(PolishOutcome("ok", "测试", None))
    monkeypatch.setattr(server, "_polisher", fake)

    async def fake_fast_path(audio_bytes, config, corrector, **kwargs):
        return _fake_fast_path_result("嗯 测试")

    monkeypatch.setattr(server, "fast_path", fake_fast_path)
    body = _post_voice(client, "1").json()
    assert body["polish_status"] == "ok"
    assert body["polished_text"] == "测试"
    assert body["display_text"] == "嗯 测试"
    assert fake.calls == ["嗯 测试"]
    assert captured[0].polished_text == "测试"
