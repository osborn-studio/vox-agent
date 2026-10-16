"""Revocable device sessions. Only credential digests are persisted."""

from __future__ import annotations

import secrets
from fastapi import HTTPException

from hashlib import sha256


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class RenewableSessions:
    access_seconds = 3600
    idle_seconds = 90 * 86400

    def initialize(self):
        self.renewing = set()
        self.renew_starts = []
        self.last_prune = float("-inf")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS login_device (
                id TEXT PRIMARY KEY, refresh_hash TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS login_session_expiry ON login_session(expires);
            CREATE TABLE IF NOT EXISTS login_session_identity (
                hash TEXT PRIMARY KEY, app_id TEXT NOT NULL, tenant TEXT NOT NULL, org_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS login_device_access (
                hash TEXT PRIMARY KEY, device_id TEXT NOT NULL);
        """)

        # Bound inherited 24-hour sessions without extending them on later starts.
        with self.db:
            self.prune_expired()
            self.db.execute(
                """UPDATE login_session SET expires=MIN(expires,?)
                WHERE hash NOT IN (SELECT hash FROM login_device_access)""",
                (self.clock() + self.access_seconds,),
            )

    def prune_expired(self):
        """Called within the caller's transaction; keep ownership proof for 90 days."""
        now = self.clock()
        if now - self.last_prune < 86400:
            return
        cutoff = now - self.idle_seconds
        self.db.execute(
            "DELETE FROM login_device_access WHERE hash IN (SELECT hash FROM login_session WHERE expires<?)",
            (cutoff,),
        )
        self.db.execute(
            "DELETE FROM login_session_identity WHERE hash IN (SELECT hash FROM login_session WHERE expires<?)",
            (cutoff,),
        )
        self.db.execute("DELETE FROM login_session WHERE expires<?", (cutoff,))
        self.db.execute(
            """UPDATE login_session SET revoked=1 WHERE hash IN
            (SELECT a.hash FROM login_device_access a JOIN login_device d ON d.id=a.device_id
             WHERE d.revoked=1 OR d.expires<=?)""",
            (now,),
        )
        self.db.execute(
            "DELETE FROM login_device_access WHERE device_id IN (SELECT id FROM login_device WHERE revoked=1 OR expires<=?)",
            (now,),
        )
        self.db.execute("DELETE FROM login_device WHERE revoked=1 OR expires<=?", (now,))
        self.last_prune = now

    def record_session_identity(self, access, tenant, org):
        self.db.execute(
            "INSERT INTO login_session_identity VALUES (?,?,?,?)",
            (digest(access), self.app_id, tenant, org),
        )

    def create_device(self, user_id, access):
        self.prune_expired()
        count = self.db.execute(
            "SELECT COUNT(*) FROM login_device WHERE user_id=? AND revoked=0 AND expires>?",
            (user_id, self.clock()),
        ).fetchone()[0]
        if count >= 20:
            oldest = self.db.execute(
                "SELECT id FROM login_device WHERE user_id=? AND revoked=0 AND expires>? ORDER BY expires,id LIMIT 1",
                (user_id, self.clock()),
            ).fetchone()[0]
            self.db.execute("UPDATE login_device SET revoked=1 WHERE id=?", (oldest,))
            self.db.execute(
                "UPDATE login_session SET revoked=1 WHERE hash IN (SELECT hash FROM login_device_access WHERE device_id=?)",
                (oldest,),
            )
        refresh = "vox_refresh_" + secrets.token_urlsafe(32)
        device = secrets.token_urlsafe(24)
        expiry = self.clock() + self.idle_seconds
        self.db.execute(
            "INSERT INTO login_device VALUES (?,?,?,?,0)",
            (device, digest(refresh), user_id, expiry),
        )
        self.db.execute("INSERT INTO login_device_access VALUES (?,?)", (digest(access), device))
        return {"refreshToken": refresh, "refreshExpiresAt": expiry}

    def device_valid(self, access_hash):
        row = self.db.execute(
            """SELECT d.revoked FROM login_device_access a
            JOIN login_device d ON d.id=a.device_id WHERE a.hash=?""",
            (access_hash,),
        ).fetchone()
        return row is None or not row["revoked"]

    def revoke_user(self, user_id):
        with self.lock, self.db:
            self.db.execute("UPDATE login_device SET revoked=1 WHERE user_id=?", (user_id,))
            self.db.execute("UPDATE login_session SET revoked=1 WHERE user_id=?", (user_id,))

    def revoke_token(self, token):
        with self.lock, self.db:
            row = self.db.execute(
                """SELECT id FROM login_device WHERE refresh_hash=?
                UNION SELECT device_id FROM login_device_access WHERE hash=?""",
                (digest(token), digest(token)),
            ).fetchone()
            if row:
                self.db.execute("UPDATE login_device SET revoked=1 WHERE id=?", (row[0],))
                self.db.execute(
                    """UPDATE login_session SET revoked=1 WHERE hash IN
                    (SELECT hash FROM login_device_access WHERE device_id=?)""",
                    (row[0],),
                )
            self.db.execute("UPDATE login_session SET revoked=1 WHERE hash=?", (digest(token),))

    def _renewable(self, token):
        row = self.db.execute(
            "SELECT * FROM login_device WHERE refresh_hash=?", (digest(token),)
        ).fetchone()
        if not row or row["revoked"] or row["expires"] <= self.clock():
            raise HTTPException(401, "SESSION_EXPIRED")
        user = self.auth.org_store.get_user(row["user_id"])
        if (
            not user
            or user["status"] != "active"
            or not self.permits(user["id"], user["org_id"])
            or not self.auth.org_store.get_organization(user["org_id"])
        ):
            self.revoke_user(row["user_id"])
            raise HTTPException(401, "ACCOUNT_DISABLED")
        identity = self.db.execute(
            "SELECT * FROM feishu_identity WHERE user_id=? AND app_id=?",
            (row["user_id"], self.app_id),
        ).fetchone()
        if not identity:
            raise HTTPException(401, "SESSION_EXPIRED")
        return row, user, identity

    def renew(self, token):
        # This implementation uses a custom app's tenant token, never a multi-tenant
        # store-app token. Unsupported configuration cannot grant a renewal.
        with self.auth.identity_lock, self.lock:
            device, user, identity = self._renewable(token)
            if len(self.settings.tenant_orgs) != 1:
                raise HTTPException(503, "MEMBERSHIP_CHECK_UNAVAILABLE")
            now = self.clock()
            self.renew_starts = [(key, t) for key, t in self.renew_starts if t > now - 60]
            if (
                len(self.renew_starts) >= 120
                or sum(key == user["id"] for key, _ in self.renew_starts) >= 6
            ):
                raise HTTPException(429, "RENEWAL_RATE_LIMIT")
            if device["id"] in self.renewing:
                raise HTTPException(409, "RENEWAL_IN_PROGRESS")
            self.renew_starts.append((user["id"], now))
            self.renewing.add(device["id"])
        try:
            try:
                active = self.provider.member_active(identity["tenant"], identity["open_id"])
            except Exception:
                raise HTTPException(503, "MEMBERSHIP_CHECK_UNAVAILABLE") from None
            with self.auth.identity_lock, self.lock:
                current, user, identity = self._renewable(token)
                if active is not True:
                    self.revoke_user(user["id"])
                    raise HTTPException(401, "ACCOUNT_DISABLED")
                access = "vox_sso_" + secrets.token_urlsafe(32)
                expires = self.clock() + self.access_seconds
                idle = self.clock() + self.idle_seconds
                with self.db:
                    self.prune_expired()
                    # Keep old access valid until its short expiry, so in-flight
                    # requests survive renewal. Logout revokes every family token.
                    self.db.execute(
                        "INSERT INTO login_session VALUES (?,?,?,0)",
                        (digest(access), user["id"], expires),
                    )
                    self.record_session_identity(access, identity["tenant"], user["org_id"])
                    self.db.execute(
                        "INSERT INTO login_device_access VALUES (?,?)",
                        (digest(access), current["id"]),
                    )
                    self.db.execute(
                        "UPDATE login_device SET expires=? WHERE id=?", (idle, current["id"])
                    )
                result = self.me(access)
                result.update(
                    token=access,
                    expiresAt=expires,
                    expiresIn=self.access_seconds,
                    refreshToken=token,
                    refreshExpiresAt=idle,
                )
                return result
        finally:
            with self.lock:
                self.renewing.discard(device["id"])
