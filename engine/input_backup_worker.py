"""Single-worker Feishu delivery. HTTP bodies/errors are never logged.

The host owns transport/client lifetime and secret-store integration. The secret
callable must read a protected credential store, not an environment/file literal.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx

from .input_backup import BackupStore


class FeishuTransport:
    def __init__(self, app_id: str, secret: Callable[[], str], *, client: httpx.Client):
        self.app_id = app_id
        self.secret = secret
        self.client = client
        self._token = None
        self._expires = 0.0

    def _access_token(self):
        if self._token and time.monotonic() < self._expires:
            return self._token
        response = self.client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.secret()},
            timeout=15,
            follow_redirects=False,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or type(data.get("code")) is not int or data["code"] != 0:
            raise ValueError("token unavailable")
        token, expire = data.get("tenant_access_token"), data.get("expire")
        if not isinstance(token, str) or not token or type(expire) is not int or expire <= 60:
            raise ValueError("invalid token response")
        self._token, self._expires = token, time.monotonic() + expire - 60
        return token

    def send(self, job):
        try:
            token = self._access_token()
        except Exception:
            return "not_sent", None
        try:
            response = self.client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": job["chat"],
                    "msg_type": "text",
                    "content": json.dumps({"text": job["rendered"]}, ensure_ascii=False),
                    "uuid": job["delivery_uuid"],
                },
                timeout=15,
                follow_redirects=False,
            )
            if response.status_code == 401:
                self._token = None
            # Conservatively retain uncertainty, even for an error envelope.
            # Never infer non-delivery merely from a proxy/server failure.
            if response.status_code != 200:
                self._token = None
                return "unknown", None
            data = response.json()
            if not isinstance(data, dict) or type(data.get("code")) is not int or data["code"] != 0:
                self._token = None
                return "unknown", None
            message = data.get("data")
            if not isinstance(message, dict):
                return "unknown", None
            message_id = message.get("message_id")
            if (
                not isinstance(message_id, str)
                or not message_id
                or message.get("chat_id") != job["chat"]
            ):
                return "unknown", None
            return "delivered", message_id
        except (httpx.HTTPError, ValueError):
            return "unknown", None


class BackupWorker:
    """Use as a context manager. One process lock per canonical database path.

    authorize must recheck active employee membership AND exact immutable target
    snapshot before each send. The required callback deliberately has no permissive
    default. Returning false quarantines the job for operator reconciliation.
    """

    def __init__(
        self, store: BackupStore, transport: FeishuTransport, *, authorize: Callable[[dict], bool]
    ):
        self.store = store
        self.transport = transport
        self.authorize = authorize
        self._lock = None

    def __enter__(self):
        import fcntl  # Deployment target is Linux/macOS, not Windows/NFS.

        if self._lock is not None:
            raise RuntimeError("worker already running")
        handle = open(str(self.store.path) + ".worker.lock", "a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError("worker already running") from None
        self._lock = handle
        try:
            self.store.recover()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def run_once(self):
        if self._lock is None:
            raise RuntimeError("worker lock required")
        job = self.store.claim()
        if job is None:
            return False
        try:
            authorized = self.authorize(job)
        except Exception:
            self.store.defer(job)
            return True
        if not authorized:
            # Not sent this time; keep any earlier possible-delivery history.
            self.store.block(job)
            return True
        try:
            status, message_id = self.transport.send(job)
        except Exception:
            status, message_id = "unknown", None
        if status == "not_sent":
            self.store.defer(job)
        else:
            self.store.finish(job["id"], status, message_id=message_id)
        return True
