"""Per-launch loopback authentication for a desktop-launched engine (T29/T29b).

The Windows desktop shell starts the engine with
``VOXAGENT_LOCAL_TOKEN_SOURCE=stdin`` and writes a fresh 256-bit secret as the
first line of the engine's standard input. The engine reads it once at
startup, then detaches stdin and drops the variable, so nothing it spawns
inherits the pipe or learns that a secret exists. No file is involved: there
is no path to share, race or mis-permission.

The secret itself never travels over the network:

* ``GET /v1/local/handshake`` with a random ``X-VoxAgent-Challenge`` returns
  HMAC-SHA256(secret, challenge): the launcher learns the listener is its own
  engine before it sends anything else.
* Every other HTTP and WebSocket request carries ``X-VoxAgent-Local-Auth:
  v1.<unix-seconds>.<nonce>.<mac>``, an HMAC over the timestamp, a single-use
  nonce, the method and the exact path and query. A request is accepted once,
  within 120 seconds. A signature that reaches anyone else (a proxy, a
  program that took over the port) authorises nothing beyond that one request.

Without the variable the engine behaves as before (the macOS app path).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import BinaryIO

from engine import __version__

HANDSHAKE_PATH = "/v1/local/handshake"
TOKEN_SOURCE_ENV = "VOXAGENT_LOCAL_TOKEN_SOURCE"
AUTH_HEADER = b"x-voxagent-local-auth"
CHALLENGE_HEADER = b"x-voxagent-challenge"
MAX_SKEW_SECONDS = 120
READ_DEADLINE_SECONDS = 10
_MAX_NONCES = 20_000
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
_CHALLENGE = re.compile(r"[0-9a-f]{32,128}")
_AUTH = re.compile(r"v1\.([0-9]{1,12})\.([0-9a-f]{32})\.([0-9a-f]{64})")


class LocalTokenError(RuntimeError):
    """The launch secret was requested but is missing or malformed."""


def load_local_token(
    environ: Mapping[str, str] | None = None, stream: BinaryIO | None = None
) -> str | None:
    """Read the launch secret when ``VOXAGENT_LOCAL_TOKEN_SOURCE`` asks for it.

    Returns None only when the variable is absent. Any other value, or a
    missing or malformed secret, raises, so a misconfigured launch never
    starts an unauthenticated engine.
    """

    env = os.environ if environ is None else environ
    if TOKEN_SOURCE_ENV not in env:
        return None
    if env[TOKEN_SOURCE_ENV] != "stdin":
        raise LocalTokenError("VOXAGENT_LOCAL_TOKEN_SOURCE must be 'stdin'")
    source = stream if stream is not None else sys.stdin.buffer
    raw = _read_line(source, READ_DEADLINE_SECONDS)
    try:
        token = raw.removesuffix(b"\n").decode("ascii")
    except UnicodeDecodeError as exc:
        raise LocalTokenError("launch secret is not ASCII") from exc
    if not _TOKEN.fullmatch(token):
        raise LocalTokenError("launch secret is malformed")
    return token


def _read_line(source: BinaryIO, deadline: float) -> bytes:
    """One bounded line, or fail if the launcher writes nothing in time (a
    silent pipe must not hang startup forever)."""

    result: list[bytes] = []
    errors: list[BaseException] = []

    def read() -> None:
        try:
            result.append(source.readline(130))
        except (OSError, ValueError) as exc:
            errors.append(exc)

    reader = threading.Thread(target=read, name="launch-secret-reader", daemon=True)
    reader.start()
    reader.join(deadline)
    if reader.is_alive():
        raise LocalTokenError("launch secret was not provided in time")
    if errors or not result:
        raise LocalTokenError("launch secret could not be read")
    return result[0]


_LAUNCH_LOCK = threading.Lock()
_LAUNCH: dict[str, str | None] = {}


def launch_secret() -> str | None:
    """The process's launch secret, read once. Later calls (another app built
    by the same process) get the same answer instead of silently losing
    authentication after the variable was dropped."""

    with _LAUNCH_LOCK:
        if "token" not in _LAUNCH:
            token = load_local_token()
            if token is not None:
                detach_launch_channel()
            _LAUNCH["token"] = token
        return _LAUNCH["token"]


def detach_launch_channel(environ: dict[str, str] | None = None) -> None:
    """After reading: point fd 0 at the null device and drop the variable, so
    tools and agents the engine starts inherit neither."""

    env = os.environ if environ is None else environ
    env.pop(TOKEN_SOURCE_ENV, None)
    try:
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.close(null)
    except OSError:
        pass


def handshake_proof(token: str, challenge: str) -> str:
    message = f"voxagent-local-handshake-v1|{challenge}".encode("ascii")
    return hmac.new(token.encode("ascii"), message, hashlib.sha256).hexdigest()


def request_mac(token: str, timestamp: int, nonce: str, method: str, target: str) -> str:
    message = f"voxagent-local-request-v1|{timestamp}|{nonce}|{method}|{target}"
    return hmac.new(token.encode("ascii"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _header(scope, name: bytes) -> str | None:
    values = [value for key, value in scope.get("headers", []) if key.lower() == name]
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _target(scope) -> str | None:
    """The exact request target as sent: raw path plus a non-empty query.
    Without the server's raw path the target cannot be reconstructed exactly,
    so the request is refused."""

    raw = scope.get("raw_path")
    if not raw:
        return None
    query = scope.get("query_string") or b""
    return raw.decode("latin-1") + ("?" + query.decode("latin-1") if query else "")


async def _json(send, status: int, body: dict) -> None:
    payload = json.dumps(body, separators=(",", ":")).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class LocalTokenGuard:
    """Outermost ASGI middleware enforcing per-request launch signatures."""

    def __init__(self, app, token: str, clock=time.time, monotonic=time.monotonic):
        self.app = app
        self._token = token
        self._clock = clock
        # Nonces are retained by the monotonic clock, so a wall-clock jump
        # cannot expire them early and let a used signature through again.
        self._monotonic = monotonic
        self._seen: OrderedDict[str, float] = OrderedDict()

    def _accept(self, scope) -> bool:
        value = _header(scope, AUTH_HEADER)
        match = _AUTH.fullmatch(value) if value else None
        if match is None:
            return False
        timestamp, nonce, mac = int(match.group(1)), match.group(2), match.group(3)
        if abs(self._clock() - timestamp) > MAX_SKEW_SECONDS:
            return False
        target = _target(scope)
        if target is None:
            return False
        method = "GET" if scope["type"] == "websocket" else scope.get("method", "")
        expected = request_mac(self._token, timestamp, nonce, method, target)
        if not hmac.compare_digest(mac, expected):
            return False
        now = self._monotonic()
        while self._seen and next(iter(self._seen.values())) < now:
            self._seen.popitem(last=False)
        if nonce in self._seen or len(self._seen) >= _MAX_NONCES:
            return False
        self._seen[nonce] = now + 2 * MAX_SKEW_SECONDS
        return True

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if kind == "http" and scope.get("path") == HANDSHAKE_PATH:
            await self._handshake(scope, send)
            return
        if self._accept(scope):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await _json(send, 401, {"detail": {"code": "LOCAL_AUTH_REQUIRED"}})

    async def _handshake(self, scope, send):
        if scope.get("method") != "GET":
            await _json(send, 405, {"detail": {"code": "METHOD_NOT_ALLOWED"}})
            return
        challenge = _header(scope, CHALLENGE_HEADER)
        if challenge is None or not _CHALLENGE.fullmatch(challenge):
            await _json(send, 400, {"detail": {"code": "INVALID_CHALLENGE"}})
            return
        await _json(
            send,
            200,
            {"proof": handshake_proof(self._token, challenge), "version": __version__},
        )
