"""Discovery metadata only. Display caches never authorize message operations."""

from collections import deque
import re
import unicodedata
import secrets
import sqlite3
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from .feishu_conversation_store import SendInput, SendingError
from .auth import Principal
from .backup_app import require_feishu_principal
from .feishu_client import CHAT_ID_PATTERN, DiscoveryError


class ConversationMappings:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS conversation_ids (
            app TEXT, tenant TEXT, user TEXT, target TEXT, id TEXT UNIQUE NOT NULL,
            PRIMARY KEY(app, tenant, user, target))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS conversation_scopes (
            id TEXT PRIMARY KEY, app TEXT, tenant TEXT, org TEXT, user TEXT, target TEXT,
            UNIQUE(app,tenant,org,user,target))""")
        self.db.commit()

    def identifiers(self, identity, targets):
        with self.lock, self.db:
            result = []
            for target in targets:
                key = (identity["app_id"], identity["tenant"], identity["user_id"], target)
                self.db.execute(
                    "INSERT OR IGNORE INTO conversation_ids VALUES (?,?,?,?,?)",
                    (*key, "fc_" + secrets.token_urlsafe(24)),
                )
                result.append(
                    self.db.execute(
                        "SELECT id FROM conversation_ids WHERE app=? AND tenant=? AND user=? AND target=?",
                        key,
                    ).fetchone()[0]
                )
            if "org_id" in identity:
                for index, target in enumerate(targets):
                    key = (identity["app_id"], identity["tenant"], identity["org_id"], identity["user_id"], target)
                    self.db.execute("INSERT OR IGNORE INTO conversation_scopes VALUES (?,?,?,?,?,?)",
                                    (result[index], *key))
                    row = self.db.execute("SELECT id FROM conversation_scopes WHERE app=? AND tenant=? AND org=? AND user=? AND target=?", key).fetchone()
                    if row is None:
                        self.db.execute("INSERT INTO conversation_scopes VALUES (?,?,?,?,?,?)",
                                        ("fc_" + secrets.token_urlsafe(24), *key))
                        row = self.db.execute("SELECT id FROM conversation_scopes WHERE app=? AND tenant=? AND org=? AND user=? AND target=?", key).fetchone()
                    result[index] = row[0]
            return result

    def resolve(self, identity, conversation):
        with self.lock:
            row = self.db.execute("SELECT target FROM conversation_scopes WHERE id=? AND app=? AND tenant=? AND org=? AND user=?",
                (conversation, identity["app_id"], identity["tenant"], identity["org_id"], identity["user_id"])).fetchone()
            return row[0] if row else None

    def close(self):
        self.db.close()


class ConversationDiscovery:
    def __init__(
        self,
        client,
        mappings,
        *,
        clock=time.monotonic,
        scan_seconds=8,
        max_lookups=12,
        max_member_ids=20_000,
    ):
        self.client, self.mappings, self.clock = client, mappings, clock
        self.scan_seconds, self.max_lookups = scan_seconds, max_lookups
        self.max_member_ids = max_member_ids
        self.lock = threading.Lock()
        self.cache = {}
        self.principals = {}
        self.failed_until = 0

    def _check_deadline(self):
        if self.clock() >= self.deadline:
            raise DiscoveryError("DISCOVERY_BUDGET_EXCEEDED")

    def _cached(self, key, load):
        self._check_deadline()
        now = self.clock()
        self.cache = {k: v for k, v in self.cache.items() if v[0] > now}
        entry = self.cache.get(key)
        if entry:
            return entry[1]
        if key[2] == "members":
            if self.lookups >= self.max_lookups:
                raise DiscoveryError("DISCOVERY_BUDGET_EXCEEDED")
            self.lookups += 1
        value = load()
        self._check_deadline()
        if key[2] == "chats":
            value = self._validated_chats(value)
        if key[2] == "members":
            if any(
                not isinstance(m.get("member_id"), str)
                or not re.fullmatch(r"ou_[A-Za-z0-9_-]{1,253}", m["member_id"])
                for m in value
            ):
                raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
            value = frozenset(m["member_id"] for m in value)
            retained = sum(len(v[1]) for k, v in self.cache.items() if k[2] == "members")
            if retained + len(value) > self.max_member_ids:
                raise DiscoveryError("DISCOVERY_BUDGET_EXCEEDED")
        self.cache[key] = (self.clock() + 300, value)
        return value

    def _validated_chats(self, chats, *, strict_eligibility=False):
        # Validate complete eligible metadata before caching. A transient bad page
        # must be refetched after cooldown, never poison the five-minute cache.
        result, seen = [], {}
        for chat in chats:
            cid, name = chat.get("chat_id"), chat.get("name")
            if not isinstance(cid, str) or not re.fullmatch(CHAT_ID_PATTERN, cid):
                raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
            if strict_eligibility and (type(chat.get("external")) is not bool or not isinstance(chat.get("tenant_key"), str) or not chat["tenant_key"]):
                raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
            eligible = chat.get("external") is False and chat.get("tenant_key") == self.client.tenant
            repeated = cid in seen
            if repeated and seen[cid] != eligible:
                raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
            seen[cid] = eligible
            if not eligible:
                continue
            if not isinstance(name, str):
                raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
            if repeated:
                continue  # Consistent page overlap; never resolve contradictory eligibility.
            name = name[:256] or "Unnamed group"
            # Render hostile display controls inert instead of letting a group
            # rename deny discovery to every user. Do not omit the group itself.
            name = "".join(
                "\ufffd" if unicodedata.category(c) in ("Cc", "Cs")
                or unicodedata.bidirectional(c)
                in ("LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI")
                else c for c in name
            )
            result.append({"chat_id": cid, "name": name})
        return result

    def list(self, identity, policy):
        if (identity["app_id"], identity["tenant"]) != (self.client.app_id, self.client.tenant):
            raise DiscoveryError("IDENTITY_MISMATCH")
        if not self.lock.acquire(blocking=False):
            raise DiscoveryError("DISCOVERY_BUSY")
        scanning = False
        try:
            now = self.clock()
            self.principals = {k: v for k, v in self.principals.items() if v[-1] > now - 60}
            principal = (identity["app_id"], identity["tenant"], identity["user_id"])
            if principal not in self.principals and len(self.principals) >= 4096:
                raise DiscoveryError("RATE_LIMITED")
            if now < self.failed_until:
                raise DiscoveryError("RATE_LIMITED")
            window = self.principals.setdefault(principal, deque())
            while window and window[0] <= now - 60:
                window.popleft()
            if len(window) >= 6:
                raise DiscoveryError("RATE_LIMITED")
            window.append(now)
            scanning = True
            self.deadline, self.lookups = now + self.scan_seconds, 0
            scope = (identity["app_id"], identity["tenant"])
            chats = self._cached(
                (*scope, "chats"), lambda: self.client.chats(deadline=self.deadline)
            )
            groups = []
            for chat in chats:
                cid, name = chat["chat_id"], chat["name"]
                if cid in policy.denied_chat_ids:
                    continue
                members = self._cached(
                    (*scope, "members", cid),
                    lambda: self.client.members(cid, deadline=self.deadline),
                )
                if identity["open_id"] in members:
                    groups.append((cid, name))
            pins = {cid: i for i, cid in enumerate(policy.pinned_chat_ids)}
            groups.sort(key=lambda g: (pins.get(g[0], len(pins)), g[1].casefold(), g[0]))
            self._check_deadline()
            ids = self.mappings.identifiers(identity, ["dm"] + [g[0] for g in groups])
            return [
                {
                    "id": ids[0],
                    "kind": "personal",
                    "name": "Feishu CLI",
                    "capability": "discovery_only",
                }
            ] + [
                {"id": id, "kind": "group", "name": group[1], "capability": "discovery_only"}
                for id, group in zip(ids[1:], groups)
            ]

        except DiscoveryError:
            if scanning:
                self.failed_until = self.clock() + 5
            raise
        finally:
            self.lock.release()


def conversation_router():
    router = APIRouter()

    @router.get("/v1/conversations")
    def discover(request: Request, principal: Principal = Depends(require_feishu_principal)):
        context = request.app.state.context
        login = context.auth_service.feishu_login
        identity = login.conversation_identity(principal)
        if identity is None:
            raise HTTPException(403, "FEISHU_PRINCIPAL_REQUIRED")
        if context.conversation_discovery is None:
            raise HTTPException(503, "CONVERSATIONS_UNAVAILABLE")
        try:
            items = context.conversation_discovery.list(identity, context.conversation_policy)
        except DiscoveryError as error:
            category = str(error)
            status = (
                429
                if category == "RATE_LIMITED"
                else 403
                if category == "PERMISSION_DENIED"
                else 503
            )
            raise HTTPException(status, category) from None
        except Exception:
            raise HTTPException(503, "CONVERSATIONS_UNAVAILABLE") from None
        if context.conversation_store is not None:
            items = [{**item, "capability": "text_send"} for item in items]
        if context.conversation_reading is not None:
            items = [
                {**item, "readCapability": "authorized_text", "translationCapability": "explicit_per_item"}
                for item in items
            ]
        return {
            "identity": {
                "appId": identity["app_id"],
                "tenantKey": identity["tenant"],
                "userId": identity["user_id"],
            },
            "conversations": items,
        }

    def sending_context(request, principal, conversation):
        context = request.app.state.context
        identity = context.auth_service.feishu_login.conversation_identity(principal)
        if identity is None:
            raise HTTPException(403, "FEISHU_PRINCIPAL_REQUIRED")
        if context.conversation_store is None or context.conversation_discovery is None:
            raise HTTPException(503, "TEXT_SEND_UNAVAILABLE")
        target = context.conversation_discovery.mappings.resolve(identity, conversation)
        if target is None:
            raise HTTPException(403, "CONVERSATION_DENIED")
        return context, identity, target

    @router.post("/v1/conversations/{conversation}/messages", status_code=202)
    def submit(conversation: str, value: SendInput, request: Request,
               principal: Principal = Depends(require_feishu_principal)):
        context, identity, target = sending_context(request, principal, conversation)
        try:
            return context.conversation_store.accept(identity, conversation, target, value)
        except SendingError as error:
            code = str(error)
            status = {"EVENT_PAYLOAD_CONFLICT": 409, "RENDERED_MESSAGE_TOO_LARGE": 413,
                      "QUEUE_FULL": 429}.get(code, 503)
            raise HTTPException(status, code) from None
        except Exception:
            raise HTTPException(503, "TEXT_SEND_UNAVAILABLE") from None

    @router.get("/v1/conversations/{conversation}/messages")
    def read_messages(conversation: str, request: Request,
                      principal: Principal = Depends(require_feishu_principal)):
        from .feishu_conversation_reading import ReadingError
        context = request.app.state.context
        query = request.query_params
        if (set(query) - {"cursor", "pageSize"} or len(query.multi_items()) != len(query)
                or not re.fullmatch(r"[0-9]{1,2}", query.get("pageSize", "50"))):
            raise HTTPException(422, "INVALID_READ_QUERY")
        if context.conversation_reading is None or context.conversation_store is None:
            raise HTTPException(503, "READ_UNAVAILABLE")
        try:
            return context.conversation_reading.read(principal, conversation,
                cursor=query.get("cursor"), page_size=int(query.get("pageSize", "50")))
        except ReadingError as error:
            code = str(error)
            status = {"CONVERSATION_DENIED": 403, "AUTHORIZATION_RESET": 409,
                      "INVALID_CURSOR": 409, "INVALID_READ_QUERY": 422, "READ_LIMIT": 429}.get(code, 503)
            raise HTTPException(status, code) from None
        except DiscoveryError as error:
            raise HTTPException(429 if error.code == "RATE_LIMITED" else 503, error.code) from None
        except Exception:
            raise HTTPException(503, "READ_UNAVAILABLE") from None

    @router.get("/v1/conversations/{conversation}/receipts/{event}")
    def receipt(conversation: str, event: str, request: Request,
                principal: Principal = Depends(require_feishu_principal)):
        context, identity, _ = sending_context(request, principal, conversation)
        result = context.conversation_store.receipt(identity, conversation, event)
        if result is None:
            raise HTTPException(404, "RECEIPT_NOT_FOUND")
        return result

    return router
