"""Tests for the minimal mobile gateway app."""

from __future__ import annotations

from io import BytesIO

import httpx
import pytest

from apps.gateway.server import GatewayConfig, create_app


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _client_factory() -> callable:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/voice/text":
            payload = {
                "id": "evt-gateway-text",
                "status": "ok",
                "raw_text": "hello from gateway",
                "display_text": "hello from gateway",
                "polished_text": "hello from gateway",
                "stt_provider": "gateway-text",
                "stt_model": "text-v1",
                "target_app": "mobile",
                "target_risk_level": "low",
                "should_autopaste": True,
                "needs_review": False,
                "confidence": 0.98,
                "dictionary_applied": [],
                "latency_ms": 5,
                "session_id": "sess-mobile",
                "utterance_id": "utt-mobile",
                "output_id": "out-mobile",
                "output_mode": "paste",
                "archive_status": "queued",
                "created_at": "2026-04-04T00:00:00Z",
            }
            return httpx.Response(200, json=payload)
        if request.url.path == "/v1/voice":
            payload = {
                "id": "evt-gateway-audio",
                "status": "ok",
                "raw_text": "audio gateway",
                "display_text": "audio gateway",
                "polished_text": "audio gateway",
                "stt_provider": "whispercpp-local",
                "stt_model": "base.en-q5_1",
                "target_app": "mobile",
                "target_risk_level": "low",
                "should_autopaste": True,
                "needs_review": False,
                "confidence": 0.91,
                "dictionary_applied": [],
                "latency_ms": 150,
                "session_id": "sess-mobile",
                "utterance_id": "utt-audio",
                "output_id": "out-audio",
                "output_mode": "paste",
                "archive_status": "queued",
                "created_at": "2026-04-04T00:00:00Z",
            }
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    return lambda: httpx.AsyncClient(base_url="http://upstream.test", transport=transport)


@pytest.mark.anyio
async def test_gateway_health_reports_upstream():
    app = create_app(
        GatewayConfig(upstream_base_url="http://upstream.test"),
        client_factory=_client_factory(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "gateway"


@pytest.mark.anyio
async def test_gateway_text_forwards_to_upstream_text_voice():
    app = create_app(
        GatewayConfig(upstream_base_url="http://upstream.test"),
        client_factory=_client_factory(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        response = await client.post(
            "/v1/gateway/text",
            json={
                "text": "hello from gateway",
                "source": "feishu-bot",
                "target_app": "mobile",
                "session_id": "sess-mobile",
            },
        )
    assert response.status_code == 200
    assert response.json()["utterance_id"] == "utt-mobile"
    assert response.json()["stt_provider"] == "gateway-text"


@pytest.mark.anyio
async def test_gateway_audio_forwards_multipart_to_upstream_voice():
    app = create_app(
        GatewayConfig(upstream_base_url="http://upstream.test"),
        client_factory=_client_factory(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        response = await client.post(
            "/v1/gateway/audio",
            data={"source": "wechat-bot", "target_app": "mobile"},
            files={"audio": ("note.wav", BytesIO(b"0" * 256), "audio/wav")},
        )
    assert response.status_code == 200
    assert response.json()["utterance_id"] == "utt-audio"
    assert response.json()["stt_provider"] == "whispercpp-local"
