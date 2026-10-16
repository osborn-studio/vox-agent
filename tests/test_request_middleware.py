from __future__ import annotations

import logging
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.middleware import RequestLoggingMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/v1/sessions/{session_id}")
    async def session(session_id: str):
        return {"ok": bool(session_id)}

    return app


def test_request_middleware_echoes_safe_correlation_without_private_path(
    caplog,
) -> None:
    caplog.set_level(logging.INFO, logger="engine.middleware")
    client = TestClient(_app())

    response = client.get(
        "/v1/sessions/private-session-value?prompt=private-transcript",
        headers={"X-Request-ID": "mobile-request_123"},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "mobile-request_123"
    log_text = caplog.text
    assert "GET /v1/sessions/{session_id}" in log_text
    assert "request_id=mobile-request_123" in log_text
    assert "private-session-value" not in log_text
    assert "private-transcript" not in log_text


def test_request_middleware_replaces_untrusted_correlation_header(caplog) -> None:
    caplog.set_level(logging.INFO, logger="engine.middleware")
    client = TestClient(_app())

    response = client.get(
        "/missing",
        headers={"X-Request-ID": "unsafe id\nprivate-content"},
    )

    request_id = response.headers["x-request-id"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id)
    assert request_id != "unsafe id\nprivate-content"
    assert "<unmatched>" in caplog.text
    assert "private-content" not in caplog.text
