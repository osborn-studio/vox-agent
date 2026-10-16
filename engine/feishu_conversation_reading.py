"""Context-owned, bounded authorized text pages. No upstream body is persisted."""

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import secrets
import threading
import time

from .feishu_client import DiscoveryError
from .feishu_conversation_store import scope


class ReadingError(Exception):
    def __init__(self, code):
        super().__init__(
            code
            if code
            in {
                "CONVERSATION_DENIED",
                "AUTHORIZATION_RESET",
                "INVALID_CURSOR",
                "READ_BUSY",
                "READ_LIMIT",
                "INVALID_READ_QUERY",
            }
            else "READ_UNAVAILABLE"
        )


def cutoff_ms(value):
    delta = datetime.fromisoformat(value) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return -(-micros // 1000)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ConversationReading:
    def __init__(self, context, *, clock=time.time, monotonic=time.monotonic):
        self.context, self.clock, self.monotonic = context, clock, monotonic
        self.cursors = {}
        self.lock = threading.Lock()

    def _authorization(self, principal, conversation, deadline, *, fresh=True):
        ctx = self.context
        identity = ctx.auth_service.feishu_login.conversation_identity(principal)
        discovery = ctx.conversation_discovery
        if not ctx.conversations_enabled or identity is None:
            raise ReadingError("CONVERSATION_DENIED")
        identity = dict(identity)
        if (identity["user_id"], identity["org_id"]) != (principal.user_id, principal.org_id):
            raise ReadingError("CONVERSATION_DENIED")
        client = discovery.client
        if (identity["app_id"], identity["tenant"]) != (client.app_id, client.tenant):
            raise ReadingError("CONVERSATION_DENIED")
        target = discovery.mappings.resolve(identity, conversation)
        policy = ctx.conversation_policy.model_dump(mode="json")
        if target is None or target in policy["denied_chat_ids"]:
            raise ReadingError("CONVERSATION_DENIED")
        if target != "dm" and fresh:
            chats = discovery._validated_chats(
                client.chats(deadline=deadline, for_read=True), strict_eligibility=True
            )
            if not any(c["chat_id"] == target for c in chats):
                raise ReadingError("CONVERSATION_DENIED")
            members = client.members(target, deadline=deadline, for_read=True)
            ids = [m.get("member_id") for m in members]
            if any(
                not isinstance(m, str) or not re.fullmatch(r"ou_[A-Za-z0-9_-]{1,253}", m)
                for m in ids
            ) or len(set(ids)) != len(ids):
                raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
            if identity["open_id"] not in ids:
                raise ReadingError("CONVERSATION_DENIED")
        chat = ctx.conversation_store.dm_binding(identity) if target == "dm" else target
        mode, cutoff = ("personal", 0) if target == "dm" and chat else ("own_submissions", 0)
        now = self.clock()
        allow_model_translation = False
        for entry in policy["shared_read_chat_ids"]:
            effective = datetime.fromisoformat(entry["effective_at"]).timestamp()
            revoked = (
                datetime.fromisoformat(entry["revoked_at"]).timestamp()
                if entry["revoked_at"]
                else math.inf
            )
            if entry["chat_id"] == target and effective <= now < revoked:
                mode, cutoff = "shared_policy", cutoff_ms(entry["effective_at"])
                allow_model_translation = entry["allow_model_translation"]
        revision = digest([identity, policy, target, chat, mode, cutoff])
        return identity, target, chat, mode, cutoff, revision, allow_model_translation

    def read(self, principal, conversation, *, cursor=None, page_size=50):
        if (
            type(page_size) is not int
            or not 1 <= page_size <= 50
            or (
                cursor is not None
                and (
                    not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", cursor)
                )
            )
        ):
            raise ReadingError("INVALID_CURSOR" if cursor is not None else "INVALID_READ_QUERY")
        if not self.lock.acquire(blocking=False):
            raise ReadingError("READ_BUSY")
        try:
            return self._read(principal, conversation, cursor, page_size)
        finally:
            self.lock.release()

    def _read(self, principal, conversation, cursor, page_size):
        ctx = self.context
        client = ctx.conversation_discovery.client
        deadline = client.clock() + 8
        auth = self._authorization(principal, conversation, deadline)
        identity, target, chat, mode, cutoff, revision, shared_translation = auth
        # Traversal itself may race with canonical identity/policy changes.
        if self._authorization(principal, conversation, deadline, fresh=False) != auth:
            raise ReadingError("AUTHORIZATION_RESET")
        key = (*scope(identity), conversation, page_size)
        now = self.monotonic()
        self.cursors = {k: v for k, v in self.cursors.items() if v["expires"] > now}
        state = self.cursors.get(cursor) if cursor else None
        if cursor and (state is None or state["key"] != key):
            raise ReadingError("INVALID_CURSOR")
        if state and state["revision"] != revision:
            raise ReadingError("AUTHORIZATION_RESET")
        if state is None:
            state = dict(
                key=key,
                revision=revision,
                expires=now + 300,
                end=math.floor(self.clock()),
                snapshot=ctx.conversation_store.read_highwater(),
                before=None,
                token=None,
                seen=[],
                pages=0,
            )
        if state["pages"] >= 20:
            raise ReadingError("READ_LIMIT")
        next_state = dict(state, pages=state["pages"] + 1)
        if mode == "own_submissions":
            rows = ctx.conversation_store.original_page(
                identity, conversation, target, state["snapshot"], state["before"], page_size + 1
            )
            more = len(rows) > page_size
            rows = rows[:page_size]
            items = [
                dict(
                    messageId=r["message_id"],
                    eventId=r["event"],
                    source="submission",
                    text=r["text"],
                    createdAt=str(int(r["created"] * 1000)),
                    updatedAt=str(int(r["created"] * 1000)),
                    deleted=False,
                    updated=False,
                    placeholder=False,
                    authorKind="vox",
                    authorId=r["user_id"],
                    authorName=r["author_name"],
                    status=r["status"],
                )
                for r in rows
            ]
            if more:
                next_state["before"] = (rows[-1]["created"], rows[-1]["event"])
        else:
            params = dict(
                container_id_type="chat",
                container_id=chat,
                sort_type="ByCreateTimeDesc",
                page_size=page_size,
                start_time=str(cutoff // 1000),
                end_time=str(state["end"]),
            )
            if state["token"]:
                params["page_token"] = state["token"]
            data = client.history_page(params, deadline=deadline)
            more = data["has_more"]
            if more:
                token = data["page_token"]
                hashed = digest(token)
                if hashed in state["seen"]:
                    raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
                next_state.update(token=token, seen=state["seen"] + [hashed])
            items = []
            seen = set()
            for obj in data["items"]:
                item = self._message(obj, chat, identity, target)
                if item["messageId"] in seen:
                    raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")
                seen.add(item["messageId"])
                if cutoff <= int(item["createdAt"]) < state["end"] * 1000:
                    items.append(item)
        # Recheck complete current eligibility/membership after history I/O too.
        if (
            self._authorization(principal, conversation, deadline) != auth
            or self._authorization(principal, conversation, deadline, fresh=False) != auth
        ):
            raise ReadingError("AUTHORIZATION_RESET")
        if client.clock() >= deadline:
            raise DiscoveryError("DISCOVERY_BUDGET_EXCEEDED")
        # A successful page consumes its handle. Evict oldest handles under
        # pressure instead of denying fresh polling; evicted scrolls reset explicitly.
        if cursor:
            self.cursors.pop(cursor, None)
        next_cursor = None
        if more:
            owned = [k for k, v in self.cursors.items() if v["key"][:4] == key[:4]]
            if len(owned) >= 128:
                self.cursors.pop(owned[0])
            if len(self.cursors) >= 512:
                self.cursors.pop(next(iter(self.cursors)))
            next_cursor = secrets.token_urlsafe(32)
            self.cursors[next_cursor] = next_state
        # This capability applies only to the already-authorized page. Own actor
        # attribution was established server-side; rendered name prefixes confer nothing.
        for item in items:
            own = (
                item["source"] == "submission"
                or (item["authorKind"] == "vox" and item["authorId"] == identity["user_id"])
                or (item["authorKind"] == "feishu_user" and item["authorId"] == identity["open_id"])
            )
            item["modelTranslationAllowed"] = bool(
                item["text"] is not None
                and not item["deleted"]
                and not item["placeholder"]
                and (own or mode == "personal" or shared_translation)
            )
        return dict(
            translationCapability="explicit_per_item",
            conversationId=conversation,
            mode=mode,
            policyRevision=revision,
            items=items,
            nextCursor=next_cursor,
            hasMore=more,
        )

    def _message(self, obj, chat, identity, target):
        def invalid():
            raise DiscoveryError("UPSTREAM_PROTOCOL_ERROR")

        if not isinstance(obj, dict) or obj.get("chat_id") != chat:
            invalid()
        mid = obj.get("message_id")
        if not isinstance(mid, str) or not re.fullmatch(r"om_[A-Za-z0-9_-]{1,253}", mid):
            invalid()
        for key in ("create_time", "update_time"):
            if not isinstance(obj.get(key), str) or not re.fullmatch(r"[0-9]{1,16}", obj[key]):
                invalid()
        if int(obj["update_time"]) < int(obj["create_time"]):
            invalid()
        if any(type(obj.get(k)) is not bool for k in ("deleted", "updated")):
            invalid()
        kind = obj.get("msg_type")
        if not isinstance(kind, str) or kind not in {
            "text",
            "post",
            "image",
            "file",
            "audio",
            "media",
            "sticker",
            "interactive",
            "share_chat",
            "share_user",
            "system",
            "merge_forward",
            "folder",
            "video_chat",
            "location",
            "todo",
            "vote",
            "hongbao",
            "share_calendar_event",
            "calendar",
            "general_calendar",
        }:
            invalid()
        sender = obj.get("sender")
        if not isinstance(sender, dict) or sender.get("sender_type") not in {
            "user",
            "app",
            "anonymous",
            "unknown",
        }:
            invalid()
        sid = sender.get("id", "")
        if not isinstance(sid, str) or len(sid) > 256 or (sid != "" and not sid.isprintable()):
            invalid()
        if sender["sender_type"] in ("user", "app") and (
            sender.get("id_type") != ("open_id" if sender["sender_type"] == "user" else "app_id")
            or not re.fullmatch(
                ("ou" if sender["sender_type"] == "user" else "cli") + r"_[A-Za-z0-9_-]{1,253}", sid
            )
        ):
            invalid()
        for field, prefix in (
            ("root_id", "om"),
            ("parent_id", "om"),
            ("upper_message_id", "om"),
            ("thread_id", "omt"),
        ):
            if field in obj and (
                not isinstance(obj[field], str)
                or (
                    obj[field] != ""
                    and not re.fullmatch(prefix + r"_[A-Za-z0-9_-]{1,253}", obj[field])
                )
            ):
                invalid()
        body = obj.get("body")
        if not isinstance(body, dict) or not isinstance(body.get("content"), str):
            invalid()
        placeholder = (
            kind != "text"
            or len(body["content"].encode()) > 100_000
            or any(obj.get(k) for k in ("root_id", "parent_id", "thread_id", "upper_message_id"))
        )
        text = None
        if not obj["deleted"] and not placeholder:
            content = None
            try:
                content = json.loads(body["content"])
            except (ValueError, RecursionError):
                pass
            if not isinstance(content, dict) or not isinstance(content.get("text"), str):
                invalid()
            # Never expand an unexpected text envelope's nested quote/object.
            placeholder = set(content) != {"text"}
            if not placeholder:
                text = content["text"]
                if len(text.encode()) > 20_000 or "\x00" in text:
                    placeholder, text = True, None
        own_app = sender["sender_type"] == "app" and sid == identity["app_id"]
        if target == "dm" and not (
            own_app
            or (sender["sender_type"] == "user" and sid == identity["open_id"])
            or (
                kind == "system" and sender["sender_type"] in ("unknown", "anonymous") and sid == ""
            )
        ):
            invalid()
        actor = (
            self.context.conversation_store.message_actor(identity, target, chat, mid)
            if own_app
            else None
        )
        return dict(
            messageId=mid,
            eventId=actor["event"] if actor and actor["user_id"] == identity["user_id"] else None,
            source="feishu",
            text=text,
            createdAt=obj["create_time"],
            updatedAt=obj["update_time"],
            deleted=obj["deleted"],
            updated=obj["updated"],
            placeholder=placeholder,
            authorKind="vox" if actor else "feishu_" + sender["sender_type"],
            authorId=actor["user_id"] if actor else sid,
            authorName=actor["author_name"] if actor else None,
            status=None,
        )
