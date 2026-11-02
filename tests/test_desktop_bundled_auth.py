"""Bundled mode: the desktop shell's launch token guards every route (PR-B)."""

import io

import pytest
from fastapi.testclient import TestClient

from apps.desktop import server
from runtime.models.config import VoxAgentConfig

TOKEN = "t" * 43
LAUNCH_ID = "launch-0123456789abcdef"


@pytest.fixture
def bundled(monkeypatch):
    config = VoxAgentConfig()
    config.api_token = ""
    monkeypatch.setattr(server, "_config", config)
    monkeypatch.setattr(server, "_bundled", server.BundledLaunch(TOKEN, LAUNCH_ID))
    return TestClient(server.app)


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_requires_the_launch_token_and_echoes_the_launch_id(bundled):
    assert bundled.get("/health").status_code == 401
    assert bundled.get("/health", headers=auth("wrong")).status_code == 401
    body = bundled.get("/health", headers=auth()).json()
    assert body["status"] == "ok"
    assert body["launch_id"] == LAUNCH_ID


@pytest.mark.parametrize("path", ["/v1/identity", "/v1/dictionary", "/v1/settings/providers"])
def test_routes_without_their_own_check_are_still_guarded(bundled, path):
    assert bundled.get(path).status_code == 401


def test_browser_origins_are_refused_even_with_the_token(bundled):
    response = bundled.get("/health", headers={**auth(), "Origin": "http://evil.example"})
    assert response.status_code == 403
    preflight = bundled.options(
        "/v1/voice",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in preflight.headers


def test_development_mode_is_unchanged(monkeypatch):
    config = VoxAgentConfig()
    config.api_token = ""
    monkeypatch.setattr(server, "_config", config)
    monkeypatch.setattr(server, "_bundled", None)
    body = TestClient(server.app).get("/health").json()
    assert body["status"] == "ok"
    assert "launch_id" not in body


def test_launch_secrets_come_from_stdin_and_must_be_long_enough():
    launch = server.read_bundled_launch(io.StringIO(f"{TOKEN}\n{LAUNCH_ID}\n"))
    assert launch == server.BundledLaunch(TOKEN, LAUNCH_ID)
    with pytest.raises(SystemExit):
        server.read_bundled_launch(io.StringIO("short\nid\n"))
    with pytest.raises(SystemExit):
        server.read_bundled_launch(io.StringIO(""))


def test_bundled_mode_listens_on_loopback_only():
    with pytest.raises(SystemExit):
        server.parse_args(["--host", "0.0.0.0"])
    args = server.parse_args(["--host", "127.0.0.1", "--port", "7902", "--require-auth"])
    assert args.require_auth and args.port == 7902
