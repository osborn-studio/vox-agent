"""Bounded tenant discovery and text transport, without response logging."""

from collections import deque
from concurrent.futures import Future, TimeoutError
import re
import threading
import time

import httpx

CHAT_ID_PATTERN = r"^oc_[A-Za-z0-9]{1,253}$"

ORIGIN = "https://open.feishu.cn"


class DiscoveryError(Exception):
    """Only static diagnostic categories may cross this boundary."""

    def __init__(self, code):
        allowed = {
            "RATE_LIMITED",
            "DISCOVERY_BUSY",
            "DISCOVERY_BUDGET_EXCEEDED",
            "PERMISSION_DENIED",
            "UPSTREAM_UNAVAILABLE",
            "UPSTREAM_PROTOCOL_ERROR",
            "UPSTREAM_REJECTED",
            "PAGINATION_EXHAUSTED",
            "IDENTITY_MISMATCH",
        }
        self.code = code if code in allowed else "UPSTREAM_UNAVAILABLE"
        super().__init__(self.code)


class FeishuClient:
    def __init__(
        self, app_id, tenant, secret, *, transport=None, clock=time.monotonic, max_pages=20
    ):
        if type(max_pages) is not int or not 1 <= max_pages <= 20:
            raise ValueError("INVALID_PAGE_BUDGET")
        self.app_id, self.tenant, self.secret = app_id, tenant, secret
        self.clock, self.max_pages = clock, max_pages
        self.http = httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=10,
            limits=httpx.Limits(max_connections=1),
        )
        self.lock = threading.RLock()
        self.wire_lock = threading.Lock()
        self.token = None
        self.expires = 0
        self.blocked_until = 0
        # One client per application context, shared across users and endpoints.
        self.windows = {}
        self.recipient_windows = {}

    def close(self):
        self.token = None
        # A bounded wire attempt may outlive its caller deadline. Join it before
        # closing resources, including on service shutdown.
        with self.wire_lock:
            self.http.close()

    def _reserve(self, endpoint):
        now = self.clock()
        if now < self.blocked_until:
            raise DiscoveryError("RATE_LIMITED")
        keys = [("app", self.app_id), ("tenant", self.tenant), ("endpoint", endpoint)]
        if endpoint.startswith("read:"):
            keys.append(("read", self.app_id))
        for key in keys:
            window = self.windows.setdefault(key, deque())
            while window and window[0] <= now - 60:
                window.popleft()
            # Conservative local budgets, not a claim of live tenant capacity.
            minute_limit, second_limit = (150, 10) if key[0] == "read" else (300, 20)
            if len(window) >= minute_limit or sum(t > now - 1 for t in window) >= second_limit:
                raise DiscoveryError("RATE_LIMITED")
        for key in keys:
            self.windows[key].append(now)

    def _request(self, method, path, endpoint, *, deadline=None, **kwargs):
        remaining = min(10, deadline - self.clock()) if deadline is not None else 10
        if remaining <= 0:
            raise DiscoveryError("DISCOVERY_BUDGET_EXCEEDED")
        # httpx timeouts are per I/O phase, not a total deadline. One bounded
        # daemon worker covers slow headers/body too. If it outlives the caller,
        # all new work fails immediately until it exits: never queue more work.
        if not self.wire_lock.acquire(blocking=False):
            raise DiscoveryError("DISCOVERY_BUSY")
        result = Future()

        def perform():
            value, failure = None, None
            try:
                value = self._request_sync(method, path, endpoint, deadline=deadline, **kwargs)
            except DiscoveryError as error:
                failure = error.code
            except Exception:
                failure = "UPSTREAM_UNAVAILABLE"
            finally:
                self.wire_lock.release()
            result.set_result((value, failure))

        threading.Thread(target=perform, daemon=True, name="feishu-discovery").start()
        value, failure = None, None
        try:
            value, failure = result.result(timeout=remaining)
        except TimeoutError:
            failure = "DISCOVERY_BUDGET_EXCEEDED"
        if failure:
            raise DiscoveryError(failure)
        return value

    def _request_sync(self, method, path, endpoint, *, deadline=None, **kwargs):
        now = self.clock()
        if deadline is not None and now >= deadline:
            raise DiscoveryError("DISCOVERY_BUDGET_EXCEEDED")
        request_deadline = min(deadline, now + 10) if deadline is not None else now + 10
        self._reserve(endpoint)
        response, failure, status = None, None, 0
        headers = {**kwargs.pop("headers", {}), "Accept-Encoding": "identity"}
        try:
            with self.http.stream(
                method,
                ORIGIN + path,
                headers=headers,
                timeout=max(0.001, request_deadline - now),
                **kwargs,
            ) as stream:
                status = stream.status_code
                if status == 401:
                    self.token = None
                if status == 429:
                    self.blocked_until = self.clock() + 60
                    raise DiscoveryError("RATE_LIMITED")
                if status in (401, 403):
                    raise DiscoveryError("PERMISSION_DENIED")
                if status != 200 and not 400 <= status < 500:
                    raise DiscoveryError("UPSTREAM_UNAVAILABLE")
                if stream.headers.get("content-encoding", "identity").lower() != "identity":
                    raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
                body = bytearray()
                # MockTransport may supply an already buffered response; live responses
                # always use raw wire bytes, with compression explicitly forbidden.
                chunks = [stream.content] if stream.is_stream_consumed else stream.iter_raw()
                for chunk in chunks:
                    if self.clock() >= request_deadline:
                        raise DiscoveryError("DISCOVERY_BUDGET_EXCEEDED")
                    if len(body) + len(chunk) > 1_048_576:
                        raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
                    body.extend(chunk)
                if self.clock() >= request_deadline:
                    raise DiscoveryError("DISCOVERY_BUDGET_EXCEEDED")
                response = httpx.Response(status, content=bytes(body)).json()
        except DiscoveryError as error:
            failure = error.code
        except httpx.TransportError:
            failure = "UPSTREAM_UNAVAILABLE"
        except Exception:
            failure = "UPSTREAM_PROTOCOL_ERROR"
        # Raise outside handlers so no upstream exception/body survives in a chain.
        if failure:
            raise DiscoveryError(failure)
        if not isinstance(response, dict) or type(response.get("code")) is not int:
            raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
        if response["code"] in (230020, 99991400):
            self.blocked_until = self.clock() + 60
            raise DiscoveryError("RATE_LIMITED")
        if response["code"] in (99991661, 99991663):
            self.token = None
            raise DiscoveryError("PERMISSION_DENIED")
        if response["code"] in (230027, 99991672):
            raise DiscoveryError("PERMISSION_DENIED")
        if response["code"] != 0 or status != 200:
            raise DiscoveryError("UPSTREAM_REJECTED")
        return response

    def _token(self, deadline=None):
        if self.token and self.clock() < self.expires:
            return self.token
        secret = None
        try:
            secret = self.secret()
        except Exception:
            pass
        if not isinstance(secret, str) or not secret:
            raise DiscoveryError("UPSTREAM_UNAVAILABLE")
        data = self._request(
            "POST",
            "/open-apis/auth/v3/tenant_access_token/internal",
            "token",
            json={"app_id": self.app_id, "app_secret": secret},
            deadline=deadline,
        )
        token, expiry = data.get("tenant_access_token"), data.get("expire")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 8192
            or not token.isascii()
            or not token.isprintable()
            or any(c.isspace() for c in token)
            or type(expiry) is not int
            or not 60 < expiry <= 86400
        ):
            raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
        self.token, self.expires = token, self.clock() + expiry - 60
        return token

    def _pages(self, path, endpoint, params, deadline=None):
        if not self.lock.acquire(blocking=False):
            raise DiscoveryError("DISCOVERY_BUSY")
        try:
            token = self._token(deadline)
            items, seen = [], set()
            for _ in range(self.max_pages):
                result = self._request(
                    "GET",
                    path,
                    endpoint,
                    params=params,
                    deadline=deadline,
                    headers={"Authorization": "Bearer " + token},
                )
                data = result.get("data")
                if (
                    not isinstance(data, dict)
                    or type(data.get("has_more")) is not bool
                    or not isinstance(data.get("items"), list)
                    or len(data["items"]) > 100
                    or not all(isinstance(x, dict) for x in data["items"])
                ):
                    raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
                items.extend(data["items"])
                if not data["has_more"]:
                    return items
                cursor = data.get("page_token")
                if not isinstance(cursor, str) or not 0 < len(cursor) <= 4096 or cursor in seen:
                    raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
                seen.add(cursor)
                params["page_token"] = cursor
            raise DiscoveryError("PAGINATION_EXHAUSTED")

        finally:
            self.lock.release()

    def member_active(self, open_id):
        """Employment status is privileged; missing fields never mean active."""
        if not isinstance(open_id, str) or not re.fullmatch(r"ou_[A-Za-z0-9_-]{1,253}", open_id):
            raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
        deadline = self.clock() + 15
        with self.lock:
            token = self._token(deadline)
            body = self._request("GET", "/open-apis/contact/v3/users/" + open_id, "membership",
                params={"user_id_type": "open_id"}, deadline=deadline,
                headers={"Authorization": "Bearer " + token})
        data = body.get("data")
        user = data.get("user") if isinstance(data, dict) else None
        if not isinstance(user, dict) or user.get("open_id") != open_id:
            raise DiscoveryError("IDENTITY_MISMATCH")
        status = user.get("status")
        flags = ("is_activated", "is_frozen", "is_resigned", "is_exited", "is_unjoin")
        if not isinstance(status, dict) or any(type(status.get(k)) is not bool for k in flags):
            raise DiscoveryError("PERMISSION_DENIED")
        return status["is_activated"] and not any(status[k] for k in flags[1:])

    def chats(self, *, deadline=None, for_read=False):
        return self._pages(
            "/open-apis/im/v1/chats",
            "read:chats" if for_read else "chats",
            {"page_size": 50, "user_id_type": "open_id"},
            deadline,
        )

    def members(self, chat_id, *, deadline=None, for_read=False):
        if not isinstance(chat_id, str) or not re.fullmatch(CHAT_ID_PATTERN, chat_id):
            raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
        return self._pages(
            f"/open-apis/im/v1/chats/{chat_id}/members",
            "read:members" if for_read else "members",
            {"page_size": 50, "member_id_type": "open_id"},
            deadline,
        )

    def history_page(self, params, *, deadline):
        if not self.lock.acquire(blocking=False):
            raise DiscoveryError("DISCOVERY_BUSY")
        try:
            token = self._token(deadline)
            result = self._request("GET", "/open-apis/im/v1/messages", "read:history",
                params=params, deadline=deadline, headers={"Authorization": "Bearer " + token})
            data = result.get("data")
            if (not isinstance(data, dict) or type(data.get("has_more")) is not bool
                    or not isinstance(data.get("items"), list) or len(data["items"]) > params["page_size"]
                    or not all(isinstance(x, dict) for x in data["items"])):
                raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
            if data["has_more"] and (not isinstance(data.get("page_token"), str)
                    or not 1 <= len(data["page_token"]) <= 4096
                    or not data["page_token"].isascii() or not data["page_token"].isprintable()):
                raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
            return data
        finally:
            self.lock.release()

    def send_text(self, target, text, delivery_uuid, *, before_wire=None):
        """Send-specific business codes; all local preparation precedes durable begin.

        Shared quota backoff deliberately affects backup and conversations. There
        are no hidden message retries and no retained upstream exception/body.
        """
        import json

        if not re.fullmatch(r"(?:ou_[A-Za-z0-9_-]{1,253}|oc_[A-Za-z0-9]{1,253})", target):
            raise SendFailure(kind="failed")
        if not self.lock.acquire(blocking=False):
            raise SendFailure(kind="no_wire")
        kind, result = "no_wire", None
        owns_wire = False
        try:
            token = self._token()
            now = self.clock()
            self.recipient_windows = {k: v for k, v in self.recipient_windows.items()
                                      if v and v[-1] > now - 1}
            window = self.recipient_windows.setdefault(target, deque())
            while window and window[0] <= now - 1:
                window.popleft()
            if len(window) < 5 and len(self.recipient_windows) <= 4096:
                self._reserve("send")
                owns_wire = self.wire_lock.acquire(blocking=False)
                if owns_wire:
                    window.append(now)
                    payload = {"receive_id": target, "msg_type": "text",
                               "content": json.dumps({"text": text}, ensure_ascii=False),
                               "uuid": delivery_uuid}
                    # Last gate before actual HTTP. Token, reservations and locks
                    # cannot consume an attempted submission or its UUID window.
                    if before_wire is not None and not before_wire():
                        kind = "retired"
                    else:
                        kind = "uncertain"
                        deadline = self.clock() + 10
                        with self.http.stream("POST", ORIGIN + "/open-apis/im/v1/messages",
                            params={"receive_id_type": "open_id" if target.startswith("ou_") else "chat_id"},
                            headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"},
                            json=payload, timeout=10) as stream:
                            status = stream.status_code
                            if status == 401:
                                self.token = None
                                kind = "backoff"
                            elif status == 429:
                                self.blocked_until = self.clock() + 60
                                kind = "backoff"
                            elif status in (200, 400, 403) and stream.headers.get("content-encoding", "identity").lower() == "identity":
                                body = bytearray()
                                chunks = [stream.content] if stream.is_stream_consumed else stream.iter_raw()
                                for chunk in chunks:
                                    if self.clock() >= deadline or len(body) + len(chunk) > 1_048_576:
                                        raise ValueError("INVALID_SEND_RESPONSE")
                                    body.extend(chunk)
                                response = httpx.Response(status, content=bytes(body)).json()
                                if isinstance(response, dict) and type(response.get("code")) is int:
                                    code = response["code"]
                                    if code == 0 and status == 200:
                                        result = response.get("data")
                                        kind = None if isinstance(result, dict) else "uncertain"
                                    elif code in (99991661, 99991663):
                                        self.token = None
                                        kind = "backoff"
                                    elif code in (230020, 99991400):
                                        self.blocked_until = self.clock() + 60
                                        kind = "backoff"
                                    elif code in (230002, 232009):
                                        kind = "membership"
                                    elif code in (230001, 230006, 230013, 230015, 230017,
                                                  230018, 230019, 230022, 230025, 230027,
                                                  230099, 230028, 230029, 230034, 230035,
                                                  230036, 230038, 230053, 230054, 230055,
                                                  230075, 99991672):
                                        kind = "failed"
                                    elif code == 230049:
                                        kind = "uncertain"  # Explicitly still being sent.
        except Exception:
            # Keep the pre-wire / wire distinction, without an exception chain.
            pass
        finally:
            if owns_wire:
                self.wire_lock.release()
            self.lock.release()
        if kind:
            raise SendFailure(kind=kind)
        return result


class SendFailure(Exception):
    def __init__(self, *, kind=None, definitive=False, retryable=False):
        # Legacy test/fake callers remain compatible; production uses kind.
        self.kind = kind or ("membership" if definitive else "no_wire" if retryable else "uncertain")
        if self.kind not in ("membership", "failed", "backoff", "no_wire", "retired", "uncertain"):
            self.kind = "uncertain"
        self.definitive = self.kind in ("membership", "failed")
        self.retryable = self.kind in ("backoff", "no_wire")
        super().__init__("SEND_REJECTED" if self.definitive else "SEND_UNAVAILABLE")


class SharedFeishuBackupTransport:
    """HTTP-only adapter: keep BackupStore/worker semantics and immutable target.

    When both features run, both paths share one app/tenant/endpoint/recipient
    budget and token. This never routes a backup through the conversation outbox.
    """

    def __init__(self, client):
        self.client = client

    def send(self, job):
        result, failure = None, None
        try:
            result = self.client.send_text(job["chat"], job["rendered"], job["delivery_uuid"])
        except SendFailure as error:
            failure = "not_sent" if error.kind in ("no_wire", "backoff", "membership", "failed", "retired") else "unknown"
        except Exception:
            failure = "unknown"
        if failure:
            return failure, None
        if (isinstance(result, dict) and result.get("chat_id") == job["chat"]
                and isinstance(result.get("message_id"), str)
                and re.fullmatch(r"om_[A-Za-z0-9_-]{1,253}", result["message_id"])):
            return "delivered", result["message_id"]
        return "unknown", None
