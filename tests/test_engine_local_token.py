"""T29/T29b: a desktop-launched engine accepts only its own launcher.

With ``VOXAGENT_LOCAL_TOKEN_SOURCE=stdin`` the engine reads a per-launch
secret from stdin. The launcher first checks the listener with a challenge
only the secret holder can answer, then signs every request (timestamp,
single-use nonce, method, exact target). The secret itself is never sent.
Without the variable nothing changes (macOS app path).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import secrets

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from engine import __version__
from engine.app_context import create_app_context
from engine.local_auth import (
    HANDSHAKE_PATH,
    MAX_SKEW_SECONDS,
    TOKEN_SOURCE_ENV,
    LocalTokenError,
    LocalTokenGuard,
    detach_launch_channel,
    handshake_proof,
    load_local_token,
    request_mac,
)
from engine.server import create_app

TOKEN = "Wq3y_local-token-0123456789abcdefghijklmnop"
CHALLENGE = "0123456789abcdef0123456789abcdef"


def sign(method: str, target: str, token: str = TOKEN, ts: int | None = None, nonce: str | None = None) -> dict:
    ts = int(__import__("time").time()) if ts is None else ts
    nonce = nonce or secrets.token_hex(16)
    return {"X-VoxAgent-Local-Auth": f"v1.{ts}.{nonce}.{request_mac(token, ts, nonce, method, target)}"}


def _client(tmp_path, token: str | None = TOKEN) -> TestClient:
    context = create_app_context(data_dir=tmp_path / "data", use_terminal_bridge=False)
    return TestClient(create_app(context, local_token=token))


def test_without_a_launch_secret_the_engine_behaves_as_before(tmp_path):
    with _client(tmp_path, token=None) as client:
        assert client.get("/health").status_code == 200
        assert client.get(HANDSHAKE_PATH, headers={"X-VoxAgent-Challenge": CHALLENGE}).status_code == 404


def test_every_request_needs_a_valid_signature(tmp_path):
    with _client(tmp_path) as client:
        for path in ("/health", "/v1/tasks", "/docs", "/openapi.json"):
            response = client.get(path)
            assert response.status_code == 401, path
            assert response.json() == {"detail": {"code": "LOCAL_AUTH_REQUIRED"}}
        assert client.get("/health", headers=sign("GET", "/health")).json()["status"] == "ok"
        # Signed for another path, method, query or secret: refused.
        assert client.get("/health", headers=sign("GET", "/v1/tasks")).status_code == 401
        assert client.get("/health", headers=sign("POST", "/health")).status_code == 401
        assert client.get("/health?x=1", headers=sign("GET", "/health")).status_code == 401
        assert client.get("/health", headers=sign("GET", "/health", token="other" + TOKEN)).status_code == 401
        # Browser preflight (DNS rebinding, a malicious page) is refused too.
        preflight = client.options(
            "/v1/tasks", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"}
        )
        assert preflight.status_code == 401


def test_a_signature_works_once_and_only_while_fresh(tmp_path):
    with _client(tmp_path) as client:
        headers = sign("GET", "/health")
        assert client.get("/health", headers=headers).status_code == 200
        assert client.get("/health", headers=headers).status_code == 401, "replay"
        now = int(__import__("time").time())
        stale = sign("GET", "/health", ts=now - MAX_SKEW_SECONDS - 5)
        future = sign("GET", "/health", ts=now + MAX_SKEW_SECONDS + 5)
        assert client.get("/health", headers=stale).status_code == 401
        assert client.get("/health", headers=future).status_code == 401
        for bad in ("v1.x.y.z", "v2." + headers["X-VoxAgent-Local-Auth"][3:], ""):
            assert client.get("/health", headers={"X-VoxAgent-Local-Auth": bad}).status_code == 401


def _scope(headers, raw_path=b"/health", query=b""):
    scope = {"type": "http", "method": "GET", "path": "/health", "query_string": query,
             "headers": [(b"x-voxagent-local-auth", headers["X-VoxAgent-Local-Auth"].encode())]}
    if raw_path is not None:
        scope["raw_path"] = raw_path
    return scope


def test_the_nonce_cache_is_bounded_and_fails_closed():
    wall, mono = [1_000_000.0], [50.0]
    guard = LocalTokenGuard(app=None, token=TOKEN, clock=lambda: wall[0], monotonic=lambda: mono[0])
    guard._seen.update({f"{i:032x}": mono[0] + 10 for i in range(20_000)})
    scope = _scope(sign("GET", "/health", ts=int(wall[0])))
    assert guard._accept(scope) is False, "full cache refuses instead of forgetting"
    mono[0] += 11
    assert guard._accept(scope) is True, "expired nonces are pruned"


def test_a_wall_clock_jump_cannot_replay_a_used_signature():
    wall, mono = [1_000_000.0], [50.0]
    guard = LocalTokenGuard(app=None, token=TOKEN, clock=lambda: wall[0], monotonic=lambda: mono[0])
    headers = sign("GET", "/health", ts=int(wall[0]))
    assert guard._accept(_scope(headers)) is True
    wall[0] += 10_000  # clock jumps forward: nonces are still retained
    assert guard._accept(_scope(headers)) is False
    wall[0] -= 10_000  # and back again within the signature window
    assert guard._accept(_scope(headers)) is False, "replay after a clock jump"


def test_an_exact_target_is_required():
    guard = LocalTokenGuard(app=None, token=TOKEN)
    assert guard._accept(_scope(sign("GET", "/health"), raw_path=None)) is False, "no raw path: refused"
    assert guard._accept(_scope(sign("GET", "/h%C3%A9", ), raw_path=b"/h%C3%A9")) is True
    assert guard._accept(_scope(sign("GET", "/health?a=1"), query=b"a=1")) is True


def test_the_secret_never_appears_in_a_response(tmp_path):
    with _client(tmp_path) as client:
        for response in (
            client.get("/health"),
            client.get(HANDSHAKE_PATH, headers={"X-VoxAgent-Challenge": CHALLENGE}),
            client.get("/health", headers=sign("GET", "/health")),
        ):
            assert TOKEN not in response.text
            assert all(TOKEN not in value for value in response.headers.values())


def test_handshake_proves_possession_without_revealing_the_secret(tmp_path):
    with _client(tmp_path) as client:
        response = client.get(HANDSHAKE_PATH, headers={"X-VoxAgent-Challenge": CHALLENGE})
        assert response.status_code == 200
        expected = hmac.new(
            TOKEN.encode(), f"voxagent-local-handshake-v1|{CHALLENGE}".encode(), hashlib.sha256
        ).hexdigest()
        assert response.json() == {"proof": expected, "version": __version__}
        assert handshake_proof(TOKEN, CHALLENGE) == expected
        assert response.headers["cache-control"] == "no-store"
        for bad in ("", "short", "z" * 32, "0" * 129, CHALLENGE + "\n"):
            assert client.get(HANDSHAKE_PATH, headers={"X-VoxAgent-Challenge": bad}).status_code == 400, repr(bad)
        assert client.post(HANDSHAKE_PATH, headers={"X-VoxAgent-Challenge": CHALLENGE}).status_code == 405


def test_websocket_needs_a_signature(tmp_path):
    with _client(tmp_path) as client:
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/ws"):
                pass
        assert refused.value.code == 1008
        with client.websocket_connect("/ws", headers=sign("GET", "/ws")) as ws:
            assert ws is not None


def test_the_secret_is_read_from_stdin_only_when_asked():
    assert load_local_token({}, io.BytesIO(TOKEN.encode() + b"\n")) is None
    assert load_local_token({TOKEN_SOURCE_ENV: "stdin"}, io.BytesIO(TOKEN.encode() + b"\n")) == TOKEN
    assert load_local_token({TOKEN_SOURCE_ENV: "stdin"}, io.BytesIO(TOKEN.encode())) == TOKEN


@pytest.mark.parametrize("value", ["", "file", "STDIN"])
def test_any_other_source_value_fails_closed(value):
    with pytest.raises(LocalTokenError):
        load_local_token({TOKEN_SOURCE_ENV: value}, io.BytesIO(TOKEN.encode()))


@pytest.mark.parametrize(
    "data", [b"", b"short\n", b"has space 0123456789abcdefghijklmnopqrstuv\n", b"x" * 300, "é".encode() * 20]
)
def test_a_missing_or_malformed_secret_stops_the_engine(data):
    with pytest.raises(LocalTokenError):
        load_local_token({TOKEN_SOURCE_ENV: "stdin"}, io.BytesIO(data))


def test_a_silent_launcher_cannot_hang_startup(monkeypatch):
    import engine.local_auth as local_auth

    read_end, write_end = os.pipe()
    monkeypatch.setattr(local_auth, "READ_DEADLINE_SECONDS", 0.2)
    try:
        with os.fdopen(read_end, "rb", buffering=0) as stream, pytest.raises(LocalTokenError):
            load_local_token({TOKEN_SOURCE_ENV: "stdin"}, stream)
    finally:
        os.close(write_end)


def test_the_launch_secret_is_read_once_per_process(monkeypatch):
    import engine.local_auth as local_auth

    calls = []
    monkeypatch.setattr(local_auth, "_LAUNCH", {})
    monkeypatch.setattr(local_auth, "load_local_token", lambda: calls.append(1) or TOKEN)
    monkeypatch.setattr(local_auth, "detach_launch_channel", lambda: None)
    assert local_auth.launch_secret() == TOKEN
    assert local_auth.launch_secret() == TOKEN, "a second app keeps authentication"
    assert calls == [1]


def test_after_reading_the_variable_is_dropped_and_stdin_detached(monkeypatch):
    environ = {TOKEN_SOURCE_ENV: "stdin", "OTHER": "kept"}
    saved = os.dup(0)
    try:
        detach_launch_channel(environ)
        assert environ == {"OTHER": "kept"}
        assert os.read(0, 10) == b"", "fd 0 now reads the null device"
    finally:
        os.dup2(saved, 0)
        os.close(saved)
