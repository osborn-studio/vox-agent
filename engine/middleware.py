"""Request Middleware — cross-cutting concerns for all API requests.

Uses raw ASGI middleware (not BaseHTTPMiddleware) to avoid
blocking WebSocket connections.
"""

from __future__ import annotations

import logging
import re
import time
from uuid import uuid4

logger = logging.getLogger(__name__)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_REQUEST_ID_HEADER = b"x-request-id"


def _request_id(scope) -> str:
    headers = dict(scope.get("headers", []))
    try:
        candidate = headers.get(_REQUEST_ID_HEADER, b"").decode("ascii")
    except UnicodeDecodeError:
        candidate = ""
    return candidate if _REQUEST_ID.fullmatch(candidate) else uuid4().hex


def _route_template(scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path.startswith("/") else "<unmatched>"


class RequestLoggingMiddleware:
    """Log privacy-safe HTTP metadata and echo a bounded correlation ID.

    Raw ASGI middleware — compatible with WebSocket (passes through).
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            # Pass WebSocket and lifespan through unchanged
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        method = scope.get("method", "")
        request_id = _request_id(scope)
        status_code = 0

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
                headers = list(message.get("headers", []))
                headers = [
                    (name, value)
                    for name, value in headers
                    if name.lower() != _REQUEST_ID_HEADER
                ]
                headers.append((_REQUEST_ID_HEADER, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = (time.monotonic() - start) * 1000
            route_template = _route_template(scope)
            if route_template != "/health":
                logger.info(
                    "%s %s → %d (%.1fms) request_id=%s",
                    method,
                    route_template,
                    status_code,
                    duration,
                    request_id,
                )
