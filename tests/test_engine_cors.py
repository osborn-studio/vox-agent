"""CORS middleware tests — the browser-based web console reaches the engine
cross-origin, so the engine must answer preflight and echo the allowed origin.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


def _reload_server(monkeypatch, origins_env=None):
    """Reimport engine.server so the module-level CORS config re-reads env."""
    if origins_env is None:
        monkeypatch.delenv("VOXAGENT_CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("VOXAGENT_CORS_ORIGINS", origins_env)
    import engine.server as server
    return importlib.reload(server)


class TestCors:
    def test_default_allows_web_console_origin(self, monkeypatch):
        server = _reload_server(monkeypatch)
        client = TestClient(server.app)
        origin = "http://localhost:5273"
        r = client.get("/health", headers={"Origin": origin})
        assert r.headers.get("access-control-allow-origin") == origin

    def test_preflight_options_allowed(self, monkeypatch):
        server = _reload_server(monkeypatch)
        client = TestClient(server.app)
        origin = "http://localhost:5273"
        r = client.options(
            "/v1/agents/query",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert r.status_code in (200, 204)
        assert r.headers.get("access-control-allow-origin") == origin

    def test_unlisted_origin_not_echoed(self, monkeypatch):
        server = _reload_server(monkeypatch)
        client = TestClient(server.app)
        r = client.get("/health", headers={"Origin": "http://evil.example.com"})
        assert r.headers.get("access-control-allow-origin") != "http://evil.example.com"

    def test_env_override_allowlist(self, monkeypatch):
        server = _reload_server(monkeypatch, "http://example.test")
        client = TestClient(server.app)
        r = client.get("/health", headers={"Origin": "http://example.test"})
        assert r.headers.get("access-control-allow-origin") == "http://example.test"

    def test_wildcard_allows_any_origin(self, monkeypatch):
        server = _reload_server(monkeypatch, "*")
        client = TestClient(server.app)
        r = client.get("/health", headers={"Origin": "http://anything.example"})
        assert r.headers.get("access-control-allow-origin") == "*"


@pytest.fixture(autouse=True)
def _restore_server(monkeypatch):
    """Reload the module back to its default env after each test so other
    suites see the unmodified server."""
    yield
    monkeypatch.delenv("VOXAGENT_CORS_ORIGINS", raising=False)
    import engine.server as server
    importlib.reload(server)
