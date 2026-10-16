"""Server-owned Feishu OAuth identity, bounded login attempts and revocable sessions."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import Principal, require_principal
from .feishu_session_renewal import RenewableSessions, digest


class FeishuLoginSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    public_base_url: str
    tenant_orgs: dict[str, str] = Field(min_length=1)
    backup_target_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_url(self):
        p = urlsplit(self.public_base_url)
        if (
            p.username
            or p.password
            or p.query
            or p.fragment
            or p.path not in ("", "/")
            or not p.hostname
            or (
                p.scheme != "https"
                and not (p.scheme == "http" and p.hostname in ("127.0.0.1", "::1"))
            )
        ):
            raise ValueError("HTTPS or loopback origin required")
        if any(not k or not v for k, v in self.tenant_orgs.items()):
            raise ValueError("tenant and organization required")
        self.public_base_url = self.public_base_url.rstrip("/")
        return self


class SessionLink(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    previousToken: str = Field(pattern=r"^vox_sso_[A-Za-z0-9_-]{32,128}$")


class LoginBegin(BaseModel):
    """Optional /begin body. A desktop app that cannot register a URI scheme
    (Windows, owner decision D5) listens on 127.0.0.1:<loopbackPort> and the
    callback returns the browser there. The completion it carries is useless
    without the pollSecret that only the caller of /begin holds."""

    model_config = ConfigDict(extra="forbid", strict=True)
    loopbackPort: int = Field(ge=1024, le=65535)


class LoginAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    completion: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{32,128}$")
    requestId: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")


class FeishuIdentityProvider:
    def __init__(self, app_id, secret, client: httpx.Client, *, membership_transport=None):
        self.app_id, self.secret, self.client = app_id, secret, client
        self.membership_transport = membership_transport

    def member_active(self, tenant, open_id):
        from .feishu_client import FeishuClient
        client = FeishuClient(self.app_id, tenant, self.secret, transport=self.membership_transport)
        try:
            return client.member_active(open_id)
        finally:
            client.close()

    def exchange(self, code, verifier, redirect):
        # v3 rejects valid S256 PKCE with 20049 (larksuite/oapi-sdk-go#230).
        # Keep PKCE on the compatibility endpoint until v3 is verified again.
        response = self.client.post(
            "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": self.app_id,
                "client_secret": self.secret(),
                "code": code,
                "redirect_uri": redirect,
                "code_verifier": verifier,
            },
            timeout=15,
            follow_redirects=False,
        )
        response.raise_for_status()
        token_body = response.json()
        if type(token_body.get("code")) is not int or token_body["code"] != 0:
            raise ValueError("token unavailable")
        token = token_body.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("token unavailable")
        response = self.client.get(
            "https://open.feishu.cn/open-apis/authen/v1/user_info",
            headers={"Authorization": "Bearer " + token},
            timeout=15,
            follow_redirects=False,
        )
        response.raise_for_status()
        body = response.json()
        if type(body.get("code")) is not int or body["code"] != 0:
            raise ValueError("identity unavailable")
        profile = body.get("data", {})
        for key in ("open_id", "tenant_key", "name"):
            if not isinstance(profile.get(key), str) or not 0 < len(profile[key]) <= 256:
                raise ValueError("incomplete identity")
        return {key: profile[key] for key in ("open_id", "tenant_key", "name")}


class FeishuLoginService(RenewableSessions):
    def __init__(self, settings, app_id, auth, path: Path, provider, *, clock=time.time):
        self.settings, self.app_id, self.auth = settings, app_id, auth
        self.provider, self.clock = provider, clock
        self.lock = threading.RLock()
        self.attempts = {}
        self.starts = []
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        path.chmod(0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS feishu_identity (
                user_id TEXT PRIMARY KEY, app_id TEXT NOT NULL, tenant TEXT NOT NULL,
                open_id TEXT NOT NULL, name TEXT NOT NULL,
                UNIQUE(app_id,tenant,open_id));
            CREATE TABLE IF NOT EXISTS login_session (
                hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0);
        """)

        self.initialize()

    @property
    def callback_url(self):
        return self.settings.public_base_url + "/v1/auth/feishu/callback"

    def close(self):
        self.db.close()

    def begin(self, address="local", loopback_port=None):
        if loopback_port is not None and (
            type(loopback_port) is not int or not 1024 <= loopback_port <= 65535
        ):
            raise ValueError("loopback port out of range")
        with self.lock:
            now = self.clock()
            self.attempts = {
                k: v
                for k, v in self.attempts.items()
                if v["expires"] > now and v["status"] not in ("failed", "consumed")
            }
            self.starts = [(ip, t) for ip, t in self.starts if t > now - 60]
            if (
                len(self.attempts) >= 128
                or sum(ip == address for ip, _ in self.starts) >= 30
                or sum(v["address"] == address for v in self.attempts.values()) >= 5
            ):
                raise HTTPException(429, "LOGIN_RATE_LIMIT")
            self.starts.append((address, now))
            state, proof = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.attempts[state] = {
                "proof": digest(proof),
                "address": address,
                "verifier": secrets.token_urlsafe(48),
                "expires": now + 300,
                "status": "pending",
                "loopback_port": loopback_port,
            }
            return {
                "requestId": state,
                "pollSecret": proof,
                "expiresIn": 300,
                "browserURL": self.settings.public_base_url
                + "/v1/auth/feishu/browser?state="
                + state,
            }

    def attempt(self, state, proof=None):
        item = self.attempts.get(state)
        if not item or item["expires"] <= self.clock():
            raise HTTPException(410, "LOGIN_EXPIRED")
        if proof is not None and not secrets.compare_digest(item["proof"], digest(proof)):
            raise HTTPException(401, "LOGIN_PROOF_INVALID")
        return item

    def browser(self, state):
        with self.lock:
            item = self.attempt(state)
            if "cookie" in item or item["status"] != "pending":
                raise HTTPException(409, "LOGIN_ALREADY_OPENED")
            cookie = secrets.token_urlsafe(32)
            item["cookie"] = digest(cookie)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(item["verifier"].encode()).digest())
                .decode()
                .rstrip("=")
            )
            params = {
                "client_id": self.app_id,
                "redirect_uri": self.callback_url,
                "response_type": "code",
                "scope": "auth:user.id:read",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
            response = RedirectResponse(
                "https://accounts.feishu.cn/open-apis/authen/v1/authorize?" + urlencode(params)
            )
            response.set_cookie(
                "vox_login_" + state[:16],
                cookie,
                httponly=True,
                secure=self.settings.public_base_url.startswith("https:"),
                samesite="lax",
                max_age=300,
                path="/v1/auth/feishu",
            )
            return response

    def callback(self, state, cookie, code, error):
        with self.lock:
            item = self.attempt(state)
            if (
                item["status"] != "pending"
                or not item.get("cookie")
                or not cookie
                or not secrets.compare_digest(item["cookie"], digest(cookie))
            ):
                raise HTTPException(400, "LOGIN_CALLBACK_INVALID")
            item["status"] = "processing"
        try:
            if error or not code or len(code) > 4096:
                raise ValueError("authorization denied")
            profile = self.provider.exchange(code, item["verifier"], self.callback_url)
            if profile["tenant_key"] not in self.settings.tenant_orgs:
                raise ValueError("tenant not allowed")
            with self.lock:
                self.attempt(state)
                completion = secrets.token_urlsafe(32)
                item.update(status="ready", profile=profile, completion=digest(completion))
        except Exception as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            code = None
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    value = exc.response.json().get("code")
                    code = value if type(value) is int else None
                except Exception:
                    pass
            logging.getLogger(__name__).warning(
                "Feishu login failed: type=%s http_status=%s provider_code=%s",
                type(exc).__name__, status, code,
            )
            with self.lock:
                item["status"] = "failed"
        port = item.get("loopback_port")
        target = (
            f"http://127.0.0.1:{port}/vox-agent/login-complete?"
            if port
            else "com.osborn.voxagent.auth://login-complete?"
        )
        if item["status"] == "ready":
            response = RedirectResponse(
                target + urlencode({"requestId": state, "completion": completion})
            )
        else:
            response = RedirectResponse(
                target + urlencode({"requestId": state, "error": "login_failed"})
            )
        response.delete_cookie("vox_login_" + state[:16], path="/v1/auth/feishu")
        return response

    def poll(self, state, proof, completion=None):
        with self.lock:
            item = self.attempt(state, proof)
            result = {"status": item["status"]}
            if item["status"] == "ready":
                self.check_completion(item, completion)
                p = item["profile"]
                result["profile"] = {
                    "name": p["name"],
                    "appId": self.app_id,
                    "tenantKey": p["tenant_key"],
                    "openId": p["open_id"],
                }
            return result

    def check_completion(self, item, completion):
        if not completion or not secrets.compare_digest(
            item.get("completion", ""), digest(completion)
        ):
            raise HTTPException(401, "DEVICE_COMPLETION_REQUIRED")

    def redeem(self, state, proof, completion=None):
        # Match runtime lock order: identity lock before service lock.
        with self.auth.identity_lock, self.lock:
            item = self.attempt(state, proof)
            if item["status"] != "ready":
                raise HTTPException(409, "LOGIN_NOT_READY")
            self.check_completion(item, completion)
            p = item["profile"]
            org = self.settings.tenant_orgs.get(p["tenant_key"])
            if not org or not self.auth.org_store.get_organization(org):
                raise HTTPException(403, "ORGANIZATION_NOT_CONFIGURED")
            row = self.db.execute(
                "SELECT * FROM feishu_identity WHERE app_id=? AND tenant=? AND open_id=?",
                (self.app_id, p["tenant_key"], p["open_id"]),
            ).fetchone()
            user_id = (
                row["user_id"]
                if row
                else "feishu-"
                + digest(json.dumps([self.app_id, p["tenant_key"], p["open_id"]]))[:32]
            )
            user = self.auth.org_store.get_user(user_id)
            if user is None:
                from .org_models import User, OrgRole

                self.auth.org_store.insert_user(
                    User(
                        id=user_id,
                        org_id=org,
                        email=user_id + "@feishu.voxagent.invalid",
                        display_name=p["name"],
                        role=OrgRole.MEMBER,
                    )
                )
                user = self.auth.org_store.get_user(user_id)
            if user["org_id"] != org or user["status"] != "active":
                raise HTTPException(403, "ACCOUNT_DISABLED")
            token = "vox_sso_" + secrets.token_urlsafe(32)
            expires = self.clock() + self.access_seconds
            with self.db:
                self.db.execute(
                    "INSERT INTO feishu_identity VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name",
                    (user_id, self.app_id, p["tenant_key"], p["open_id"], p["name"]),
                )
                self.db.execute(
                    "INSERT INTO login_session VALUES (?,?,?,0)", (digest(token), user_id, expires)
                )
                self.record_session_identity(token, p["tenant_key"], org)
                renewal = self.create_device(user_id, token)
            item["status"] = "consumed"
            item.pop("profile", None)
            return {
                "token": token,
                "expiresAt": expires,
                "expiresIn": self.access_seconds,
                **renewal,
                "profile": {
                    "userId": user_id,
                    "orgId": org,
                    "name": p["name"],
                    "appId": self.app_id,
                    "tenantKey": p["tenant_key"],
                    "openId": p["open_id"],
                },
                "targetIds": self.settings.backup_target_ids,
            }

    def authenticate(self, token):
        with self.lock:
            row = self.db.execute(
                "SELECT user_id FROM login_session WHERE hash=? AND expires>? AND revoked=0",
                (digest(token), self.clock()),
            ).fetchone()
            if not row or not self.device_valid(digest(token)):
                return None
            user = self.auth.org_store.get_user(row["user_id"])
            if (
                not user
                or user["status"] != "active"
                or not self.permits(user["id"], user["org_id"])
            ):
                self.revoke_user(row["user_id"])
                return None
            return Principal(user["id"], user["org_id"], user["role"], "feishu")

    def resolve_conversation_principal(self, principal: Principal) -> Principal | None:
        """Recheck current identity and return its current role under canonical locks."""
        with self.auth.identity_lock, self.lock:
            if principal.auth_method != "feishu" or len(self.settings.tenant_orgs) != 1:
                return None
            user = self.auth.org_store.get_user(principal.user_id)
            if not (
                user
                and user["status"] == "active"
                and user["org_id"] == principal.org_id
                and self.auth.org_store.get_organization(principal.org_id)
                and self.permits(principal.user_id, principal.org_id)
            ):
                return None
            return Principal(user["id"], user["org_id"], user["role"], "feishu")

    def conversation_identity(self, principal):
        """Persisted mapping only; never take discovery identity from the caller."""
        with self.auth.identity_lock, self.lock:
            if self.resolve_conversation_principal(principal) is None:
                return None
            row = self.db.execute(
                "SELECT * FROM feishu_identity WHERE user_id=? AND app_id=?",
                (principal.user_id, self.app_id),
            ).fetchone()
            return {"app_id": self.app_id, "tenant": row["tenant"],
                    "user_id": principal.user_id, "open_id": row["open_id"],
                    "org_id": principal.org_id, "name": row["name"]}

    def permits(self, user_id, org_id):
        with self.lock:
            row = self.db.execute(
                "SELECT tenant FROM feishu_identity WHERE user_id=? AND app_id=?",
                (user_id, self.app_id),
            ).fetchone()
            return bool(row and self.settings.tenant_orgs.get(row["tenant"]) == org_id)

    def me(self, token):
        with self.auth.identity_lock, self.lock:
            principal = self.authenticate(token)
            if not principal:
                raise HTTPException(401, "SESSION_EXPIRED")
            row = self.db.execute(
                "SELECT * FROM feishu_identity WHERE user_id=?", (principal.user_id,)
            ).fetchone()
            expiry = self.db.execute("SELECT expires FROM login_session WHERE hash=?", (digest(token),)).fetchone()[0]
            return {
                "expiresAt": expiry,
                "expiresIn": max(0, expiry - self.clock()),
                "profile": {
                    "appId": self.app_id,
                    "userId": principal.user_id,
                    "orgId": principal.org_id,
                    "name": row["name"],
                    "tenantKey": row["tenant"],
                    "openId": row["open_id"],
                },
                "targetIds": self.settings.backup_target_ids,
            }

    def logout(self, token):
        self.revoke_token(token)
        return {"status": "signed_out"}


def login_router() -> APIRouter:
    router = APIRouter(prefix="/v1/auth/feishu")

    def service(request):
        value = getattr(request.app.state, "feishu_login", None)
        if value is None:
            raise HTTPException(503, "FEISHU_LOGIN_NOT_CONFIGURED")
        return value

    def bearer(value):
        if not value or not value.startswith("Bearer ") or len(value) > 256:
            raise HTTPException(401, "AUTH_REQUIRED")
        return value[7:]

    @router.post("/begin")
    def begin(request: Request, value: LoginBegin | None = Body(default=None)):
        return service(request).begin(
            request.client.host if request.client else "unknown",
            loopback_port=value.loopbackPort if value else None,
        )

    @router.get("/browser")
    def browser(request: Request, state: str):
        return service(request).browser(state)

    @router.get("/callback")
    def callback(request: Request, state: str, code: str = "", error: str = ""):
        return service(request).callback(
            state, request.cookies.get("vox_login_" + state[:16]), code, error
        )

    @router.post("/poll")
    def poll(
        request: Request, value: LoginAttempt, authorization: str | None = Header(default=None)
    ):
        return service(request).poll(value.requestId, bearer(authorization), value.completion)

    @router.post("/redeem")
    def redeem(
        request: Request, value: LoginAttempt, authorization: str | None = Header(default=None)
    ):
        return service(request).redeem(value.requestId, bearer(authorization), value.completion)

    @router.get("/me")
    def me(request: Request, authorization: str | None = Header(default=None)):
        return service(request).me(bearer(authorization))

    @router.post("/link-previous")
    def link_previous(request: Request, value: SessionLink, authorization: str | None = Header(default=None)):
        svc = service(request)
        with svc.auth.identity_lock, svc.lock:
            principal = svc.authenticate(bearer(authorization))
            if principal is None:
                raise HTTPException(401, "SESSION_EXPIRED")
            prior = svc.db.execute("""SELECT s.user_id, i.app_id, i.tenant, i.org_id FROM login_session s
                JOIN login_session_identity i ON s.hash=i.hash WHERE s.hash=?""", (digest(value.previousToken),)).fetchone()
            identity = svc.conversation_identity(principal)
            return {"sameIdentity": bool(prior and identity and prior["user_id"] == principal.user_id
                and prior["app_id"] == identity["app_id"] and prior["tenant"] == identity["tenant"]
                and prior["org_id"] == principal.org_id)}

    @router.post("/renew")
    def renew(request: Request, authorization: str | None = Header(default=None)):
        return service(request).renew(bearer(authorization))

    @router.post("/users/{user_id}/revoke")
    def revoke(request: Request, user_id: str, principal: Principal = Depends(require_principal)):
        svc = service(request)
        with svc.auth.identity_lock, svc.lock:
            user = svc.auth.org_store.get_user(user_id)
            actor = svc.auth.org_store.get_user(principal.user_id)
            if (principal.role != "admin" or not actor or actor["role"] != "admin" or actor["status"] != "active"
                    or actor["org_id"] != principal.org_id or not user or user["org_id"] != principal.org_id):
                raise HTTPException(403, "ADMIN_REQUIRED")
            svc.auth.audit_log.record("feishu.sessions.revoked", actor_id=principal.user_id,
                workspace_id=principal.org_id, metadata={"user_id": user_id})
            svc.revoke_user(user_id)
        return {"status": "revoked"}

    @router.post("/logout")
    def logout(request: Request, authorization: str | None = Header(default=None)):
        return service(request).logout(bearer(authorization))

    return router
