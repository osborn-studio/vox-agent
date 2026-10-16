"""Authentication and role-based authorization primitives."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import NoReturn
from uuid import uuid4

from fastapi import (
    Depends,
    Header,
    HTTPException,
    WebSocket,
    WebSocketException,
    status,
)
from starlette.requests import HTTPConnection

from .org_models import Organization, OrgRole, User
from .org_store import OrgStore
from .voice_audit import VoiceAuditLog

_MIN_TOKEN_LENGTH = 24
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Edition(str, Enum):
    PERSONAL = "personal"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    org_id: str
    role: str
    auth_method: str


class AuthService:
    """Authenticate bearer API credentials and manage local identities."""

    def __init__(
        self,
        *,
        org_store: OrgStore,
        audit_log: VoiceAuditLog,
        edition: str,
        bootstrap_admin_token: str | None,
    ) -> None:
        try:
            self.edition = Edition(edition.strip().lower())
        except ValueError as exc:
            raise RuntimeError("VOXAGENT_EDITION must be 'personal' or 'enterprise'") from exc
        self.org_store = org_store
        self.audit_log = audit_log

        if self.edition is Edition.PERSONAL:
            self._local_principal = self._ensure_admin(
                org_id="local-org",
                org_name="Personal Workspace",
                user_id="local-admin",
                email="local@voxagent.invalid",
                display_name="Local Administrator",
            )
            return

        self._local_principal = None
        admin = self._ensure_admin(
            org_id=os.environ.get("VOXAGENT_ORG_ID", "default-org"),
            org_name=os.environ.get("VOXAGENT_ORG_NAME", "Vox Agent Organization"),
            user_id="bootstrap-admin",
            email=os.environ.get("VOXAGENT_ADMIN_EMAIL", "admin@voxagent.invalid"),
            display_name=os.environ.get("VOXAGENT_ADMIN_NAME", "Bootstrap Administrator"),
        )
        if bootstrap_admin_token is not None:
            self._validate_token(bootstrap_admin_token)
            self._store_credential(
                user_id=admin.user_id,
                name="bootstrap",
                token=bootstrap_admin_token,
            )
        if self.org_store.count_active_credentials() == 0:
            raise RuntimeError(
                "enterprise edition requires VOXAGENT_BOOTSTRAP_ADMIN_TOKEN "
                "for first-time setup"
            )

    @property
    def local_principal(self) -> Principal:
        if self._local_principal is None:
            raise RuntimeError("local principal is available only in personal edition")
        return self._local_principal

    def authenticate(self, token: str) -> Principal | None:
        if self.edition is Edition.PERSONAL:
            return self.local_principal
        if not token:
            return None
        for candidate in self.org_store.list_active_auth_candidates():
            expected = _secret_hash(token, candidate["salt"])
            if not secrets.compare_digest(expected, candidate["secret_hash"]):
                continue
            if candidate["status"] != "active":
                return None
            self.org_store.touch_api_credential(candidate["credential_id"], _now_iso())
            return Principal(
                user_id=candidate["user_id"],
                org_id=candidate["org_id"],
                role=candidate["role"],
                auth_method="bearer",
            )
        return None

    def create_user(
        self,
        *,
        org_id: str,
        email: str,
        display_name: str,
        role: str = OrgRole.MEMBER.value,
    ) -> dict:
        normalized_email = email.strip().lower()
        if "@" not in normalized_email or len(normalized_email) > 254:
            raise ValueError("a valid email address is required")
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("displayName is required")
        resolved_role = OrgRole(role)
        user = User(
            org_id=org_id,
            email=normalized_email,
            display_name=normalized_name,
            role=resolved_role,
        )
        self.org_store.insert_user(user)
        return self.org_store.get_user(user.id)

    def issue_credential(self, user_id: str, name: str) -> tuple[dict, str]:
        user = self.org_store.get_user(user_id)
        if user is None or user["status"] != "active":
            raise KeyError(user_id)
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("credential name is required")
        token = secrets.token_urlsafe(32)
        credential = self._store_credential(
            user_id=user_id,
            name=normalized_name[:80],
            token=token,
        )
        return credential, token

    def revoke_credential(self, credential_id: str, *, org_id: str | None = None) -> bool:
        return self.org_store.revoke_api_credential(
            credential_id,
            _now_iso(),
            org_id=org_id,
        )

    def _ensure_admin(
        self,
        *,
        org_id: str,
        org_name: str,
        user_id: str,
        email: str,
        display_name: str,
    ) -> Principal:
        if self.org_store.get_organization(org_id) is None:
            self.org_store.insert_organization(Organization(id=org_id, name=org_name))
        user = self.org_store.get_user_by_email(org_id, email.strip().lower())
        if user is None:
            model = User(
                id=user_id,
                org_id=org_id,
                email=email.strip().lower(),
                display_name=display_name,
                role=OrgRole.ADMIN,
            )
            self.org_store.insert_user(model)
            user = self.org_store.get_user(model.id)
        return Principal(
            user_id=user["id"],
            org_id=user["org_id"],
            role=user["role"],
            auth_method="local" if self.edition is Edition.PERSONAL else "bootstrap",
        )

    def _store_credential(self, *, user_id: str, name: str, token: str) -> dict:
        salt = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii")
        credential_id = f"cred_{uuid4().hex[:16]}"
        created_at = _now_iso()
        self.org_store.upsert_api_credential(
            credential_id=credential_id,
            user_id=user_id,
            name=name,
            salt=salt,
            secret_hash=_secret_hash(token, salt),
            created_at=created_at,
        )
        return {
            "id": credential_id,
            "userId": user_id,
            "name": name,
            "createdAt": created_at,
        }

    @staticmethod
    def _validate_token(token: str) -> None:
        if len(token) < _MIN_TOKEN_LENGTH:
            raise RuntimeError(
                f"bootstrap admin token must be at least {_MIN_TOKEN_LENGTH} characters"
            )


def _secret_hash(token: str, encoded_salt: str) -> str:
    salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
    digest = hashlib.scrypt(
        token.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return base64.urlsafe_b64encode(digest).decode("ascii")


def require_principal(
    connection: HTTPConnection,
    authorization: str | None = Header(default=None),
) -> Principal:
    """Resolve the verified principal for any authenticated product route."""

    context = getattr(connection.app.state, "context", None)
    auth_service = getattr(context, "auth_service", None)
    if not isinstance(auth_service, AuthService):
        raise RuntimeError("Vox Agents AuthService is not configured")

    if auth_service.edition is Edition.PERSONAL:
        return auth_service.local_principal

    if not authorization:
        _reject_connection(
            connection,
            error_code="AUTH_REQUIRED",
            message="Bearer credential required",
        )
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        _reject_connection(
            connection,
            error_code="INVALID_CREDENTIAL",
            message="Invalid bearer credential",
        )
    principal = auth_service.authenticate(token.strip())
    if principal is None:
        _reject_connection(
            connection,
            error_code="INVALID_CREDENTIAL",
            message="Invalid bearer credential",
        )
    return principal


def require_admin(
    connection: HTTPConnection,
    principal: Principal = Depends(require_principal),
) -> Principal:
    """Require the verified principal to have the administrator role."""

    if principal.role != OrgRole.ADMIN.value:
        context = connection.app.state.context
        context.voice_audit_log.record(
            "admin.authorization.denied",
            actor_id=principal.user_id,
            workspace_id=principal.org_id,
            metadata={"reason": "admin role required", "path": connection.url.path},
        )
        if isinstance(connection, WebSocket):
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Administrator role required",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ADMIN_REQUIRED", "message": "Administrator role required"},
        )
    return principal


def _reject_connection(
    connection: HTTPConnection,
    *,
    error_code: str,
    message: str,
) -> NoReturn:
    if isinstance(connection, WebSocket):
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=message,
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": error_code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )
