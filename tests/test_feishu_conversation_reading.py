"""Authorized reading with real store/client and synthetic HTTP only."""

import json
from types import SimpleNamespace

import httpx
import pytest

from engine.auth import Principal
from engine.backup_app import ConversationPolicy, SharedReadPolicy
from engine.feishu_client import DiscoveryError
from engine.feishu_conversation_reading import ReadingError
from engine.feishu_conversation_store import ConversationStore, SendInput
from engine.feishu_conversations import ConversationDiscovery, ConversationMappings
from tests.test_feishu_conversations import client, page, chat
from tests.test_feishu_conversation_sending import IDENTITY


def message(id="om_one", **kw):
    return dict(
        message_id=id,
        chat_id="oc_group",
        create_time="1000000",
        update_time="1000000",
        deleted=False,
        updated=False,
        msg_type="text",
        body={"content": json.dumps({"text": "【CEO】untrusted"})},
        sender={
            "id": "ou_other",
            "id_type": "open_id",
            "sender_type": "user",
            "tenant_key": "team",
        },
        **kw,
    )


@pytest.fixture
def env(tmp_path):
    from engine.feishu_conversation_reading import ConversationReading

    state = SimpleNamespace(
        now=1001.0,
        identity=dict(IDENTITY),
        items=[message()],
        more=False,
        token="next",
        calls=[],
        hook=lambda: None,
        members=[{"member_id": "ou_alice"}],
        fail=False,
    )

    def wire(req):
        state.calls.append(req)
        if req.method == "POST" and req.url.path.endswith("/messages"):
            return httpx.Response(
                200, json={"code": 0, "data": {"message_id": "om_sent", "chat_id": "oc_group"}}
            )
        if req.url.path.endswith("/chats"):
            return httpx.Response(200, json=page([chat("oc_group")]))
        if req.url.path.endswith("/members"):
            if state.fail:
                return httpx.Response(500, text="private diagnostics")
            return httpx.Response(200, json=page(state.members))
        state.hook()
        return httpx.Response(
            200, json=page(state.items, has_more=state.more, page_token=state.token)
        )

    api = client(wire)
    # Test budgets must not accidentally mask authorization regressions.
    api._reserve = lambda _: None
    mappings = ConversationMappings(tmp_path / "maps.sqlite")
    store = ConversationStore(tmp_path / "store.sqlite", clock=lambda: state.now)
    ctx = SimpleNamespace(
        conversations_enabled=True,
        conversation_policy=ConversationPolicy(),
        conversation_store=store,
        conversation_discovery=ConversationDiscovery(api, mappings),
        auth_service=SimpleNamespace(
            feishu_login=SimpleNamespace(conversation_identity=lambda _: state.identity)
        ),
    )
    reading = ConversationReading(ctx, clock=lambda: state.now, monotonic=lambda: state.now)
    state.ctx, state.reading, state.store = ctx, reading, store
    state.dm, state.group = mappings.identifiers(IDENTITY, ["dm", "oc_group"])
    state.principal = Principal("alice", "org", "member", "feishu")
    state.read = lambda conversation=None, **kw: reading.read(
        state.principal, conversation or state.group, **kw
    )
    yield state
    store.close()
    mappings.close()
    api.close()


def share(e, **kw):
    values = dict(
        chat_id="oc_group",
        created_by="admin",
        effective_at="1970-01-01T00:16:40+00:00",
        version=1,
        audit_ref="audit-1",
        scope="current_members",
    )
    e.ctx.conversation_policy = ConversationPolicy(
        shared_read_chat_ids=[SharedReadPolicy(**(values | kw))]
    )


def original(e, event="e1", target="oc_group", conversation=None):
    e.store.accept(
        IDENTITY, conversation or e.group, target, SendInput(eventId=event, text="own original")
    )


def test_unshared_leave_rejoin_and_unbound_dm_never_read_upstream(env):
    e = env
    original(e)
    assert e.read()["items"][0]["text"] == "own original"
    assert e.read()["mode"] == "own_submissions"
    e.members = []
    with pytest.raises(Exception, match="CONVERSATION_DENIED"):
        e.read()
    e.members = [{"member_id": "ou_alice"}]
    assert e.read()["items"][0]["source"] == "submission"
    assert e.read(e.dm)["items"] == []
    assert not any(r.url.path.endswith("/messages") for r in e.calls)


def test_shared_cutoff_inert_envelopes_attribution_and_withdrawal(env):
    e = env
    share(e)
    old = message("om_old")
    old.update(create_time="999999", update_time="1001000", updated=True)
    rich = message("om_rich")
    rich.update(msg_type="merge_forward", body={"content": "secret nested"})
    reply = message("om_reply")
    reply.update(root_id="om_old", body={"content": '{"text":"new","quote":{"text":"old secret"}}'})
    withdrawn = message("om_gone")
    withdrawn.update(deleted=True)
    e.items = [old, message(), rich, reply, withdrawn]
    result = e.read()
    assert result["mode"] == "shared_policy"
    assert [m["messageId"] for m in result["items"]] == ["om_one", "om_rich", "om_reply", "om_gone"]
    assert result["items"][0]["authorName"] is None
    assert result["items"][0]["authorKind"] == "feishu_user"
    assert all(m["text"] is None for m in result["items"][1:])
    assert "secret" not in json.dumps(result)
    request = next(r for r in e.calls if r.url.path.endswith("/messages"))
    assert request.url.params["start_time"] == "1000"
    assert request.url.params["container_id_type"] == "chat"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda e: share(e, audit_ref="changed"),
        lambda e: share(e, revoked_at="1970-01-01T00:16:41+00:00"),
        lambda e: setattr(e, "identity", {**IDENTITY, "open_id": "ou_changed"}),
        lambda e: setattr(e, "members", []),
        lambda e: setattr(e, "fail", True),
    ],
)
def test_changes_during_io_never_return_body(env, mutation):
    e = env
    share(e)
    e.hook = lambda: mutation(e)
    with pytest.raises(
        (ReadingError, DiscoveryError),
        match="AUTHORIZATION_RESET|CONVERSATION_DENIED|UPSTREAM_UNAVAILABLE",
    ):
        e.read()


def test_cursor_scope_expiry_policy_replacement_and_cycles(env):
    e = env
    share(e)
    e.more = True
    first = e.read(page_size=1)
    cursor = first["nextCursor"]
    assert cursor and cursor != "next"
    with pytest.raises(Exception, match="CURSOR"):
        e.read(cursor="forged", page_size=1)
    with pytest.raises(Exception, match="CURSOR"):
        e.read(e.dm, cursor=cursor, page_size=1)
    with pytest.raises(Exception, match="CURSOR"):
        e.read(cursor=cursor, page_size=2)
    with pytest.raises(Exception, match="PROTOCOL"):
        e.read(cursor=cursor, page_size=1)
    e.token = "different"
    cursor = e.read(page_size=1)["nextCursor"]
    share(e, audit_ref="same-version-new-policy")
    with pytest.raises(Exception, match="RESET"):
        e.read(cursor=cursor, page_size=1)
    cursor = e.read(page_size=1)["nextCursor"]
    e.now += 301
    with pytest.raises(Exception, match="CURSOR"):
        e.read(cursor=cursor, page_size=1)


def test_same_timestamp_original_pages_and_concurrent_insert(env):
    e = env
    for event in ["a", "b", "c"]:
        original(e, event)
    first = e.read(page_size=2)
    original(e, "z")
    second = e.read(page_size=2, cursor=first["nextCursor"])
    assert [m["eventId"] for m in first["items"] + second["items"]] == ["c", "b", "a"]
    assert e.read(page_size=2)["items"][0]["eventId"] == "z"


@pytest.mark.parametrize(
    "field,value",
    [
        ("chat_id", "oc_wrong"),
        ("create_time", True),
        ("create_time", "1.2"),
        ("update_time", "-1"),
        ("deleted", 0),
        ("updated", "false"),
        ("message_id", "bad"),
        ("msg_type", "invented"),
        ("body", {"content": "not json"}),
    ],
)
def test_malformed_history_fails_closed(env, field, value):
    e = env
    share(e)
    e.items[0][field] = value
    with pytest.raises(DiscoveryError, match="PROTOCOL"):
        e.read()


def test_dm_binding_and_saved_actor_are_scoped(env):
    e = env
    original(e, target="dm", conversation=e.dm)
    job = e.store.next_job()
    e.store.complete(job, {"message_id": "om_one", "chat_id": "oc_dm"})
    e.items[0]["chat_id"] = "oc_dm"
    e.items[0]["sender"] = {"id": IDENTITY["app_id"], "id_type": "app_id", "sender_type": "app"}
    result = e.read(e.dm)
    assert result["mode"] == "personal"
    assert result["items"][0]["authorName"] == "Alice"
    assert result["items"][0]["eventId"] == "e1"
    e.identity = {**IDENTITY, "user_id": "bob"}
    with pytest.raises(Exception, match="DENIED"):
        e.read(e.dm)


def test_runtime_read_route_rejects_overrides_and_keeps_errors_no_store(tmp_path):
    from fastapi.testclient import TestClient
    from engine.backup_runtime import create_runtime_app
    from tests.test_backup_runtime import settings, secret
    from tests.test_feishu_login import Provider, login

    calls = []

    def wire(req):
        calls.append(req)
        if req.url.path.endswith("/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "synthetic", "expire": 7200}
            )
        return httpx.Response(200, json=page())

    app = create_runtime_app(
        settings(
            tmp_path,
            conversations_enabled=True,
            feishu_login={
                "public_base_url": "http://127.0.0.1:8766",
                "tenant_orgs": {"team": "default-org"},
            },
        ),
        read_secret=secret,
        conversation_transport_factory=lambda: httpx.MockTransport(wire),
    )
    with TestClient(app, base_url="http://127.0.0.1:8766") as http:
        svc = app.state.feishu_login
        svc.provider = Provider()
        result = login(svc)
        headers = {"Authorization": "Bearer " + result["token"]}
        discovery = http.get("/v1/conversations", headers=headers).json()
        dm = discovery["conversations"][0]
        assert dm["capability"] == "text_send" and dm["readCapability"] == "authorized_text"
        path = "/v1/conversations/" + dm["id"] + "/messages"
        response = http.get(path, headers=headers)
        assert response.status_code == 200 and response.json()["mode"] == "own_submissions"
        for query in [
            "?chat_id=oc_other",
            "?container_id=oc_other",
            "?pageSize=51",
            "?pageSize=1&pageSize=2",
            "?cursor=forged",
        ]:
            response = http.get(path + query, headers=headers)
            assert response.status_code in (409, 422)
            assert response.headers["cache-control"] == "no-store"
        denied = http.get("/v1/conversations/fc_forged/messages", headers=headers)
        assert denied.status_code == 403 and denied.headers["cache-control"] == "no-store"
        svc.auth.org_store.update_user(result["profile"]["userId"], status="disabled")
        assert http.get(path, headers=headers).status_code in (401, 403)
        assert not any(r.url.path.endswith("/messages") for r in calls)


def test_policy_active_interval_millisecond_cutoff_and_no_history_outside(env):
    e = env
    share(e, effective_at="1970-01-01T00:16:40.500+00:00", revoked_at="1970-01-01T00:16:42+00:00")
    e.items[0].update(create_time="1000499", update_time="1001000", updated=True)
    assert e.read()["items"] == []
    e.items[0].update(create_time="1000500")
    assert len(e.read()["items"]) == 1
    e.calls.clear()
    e.now = 1002
    assert e.read()["mode"] == "own_submissions"
    assert not any(r.url.path.endswith("/messages") for r in e.calls)
    e.now = 999
    assert e.read()["mode"] == "own_submissions"


def test_cursor_polling_independent_chain_restart_and_bounded_state(env):
    e = env
    share(e)
    e.more = True
    first = e.read()["nextCursor"]
    for _ in range(35):
        e.read()
    e.token = "older-next"
    e.items = []
    assert e.read(cursor=first)["nextCursor"]
    from engine.feishu_conversation_reading import ConversationReading

    fresh = ConversationReading(e.ctx, clock=lambda: e.now, monotonic=lambda: e.now)
    with pytest.raises(Exception, match="INVALID_CURSOR"):
        fresh.read(e.principal, e.group, cursor=first)
    for _ in range(256):
        assert e.read()["nextCursor"]
    assert len(e.reading.cursors) == 128


def test_membership_failure_unavailable_not_revoked_and_no_persisted_history(env):
    e = env
    share(e)
    assert e.read()["items"]
    with e.store.lock:
        assert e.store.db.execute("SELECT count(*) FROM submissions").fetchone()[0] == 0
    e.fail = True
    with pytest.raises(DiscoveryError, match="UPSTREAM_UNAVAILABLE"):
        e.read()
    e.fail = False
    e.members = []
    with pytest.raises(Exception, match="CONVERSATION_DENIED"):
        e.read()


def test_author_mapping_group_app_tenant_org_and_user_event_isolation(env):
    e = env
    share(e)
    bob = {**IDENTITY, "user_id": "bob", "open_id": "ou_bob", "name": "Bob"}
    e.store.accept(bob, "fc_bob", "oc_group", SendInput(eventId="same-event", text="original"))
    job = e.store.next_job()
    e.store.complete(job, {"message_id": "om_one", "chat_id": "oc_group"})
    e.items[0]["sender"] = {"id": IDENTITY["app_id"], "id_type": "app_id", "sender_type": "app"}
    item = e.read()["items"][0]
    assert item["authorName"] == "Bob" and item["eventId"] is None
    with e.store.lock, e.store.db:
        e.store.db.execute("UPDATE submissions SET org_id='other'")
    assert e.read()["items"][0]["authorKind"] == "feishu_app"


@pytest.mark.parametrize(
    "field,value",
    [
        ("root_id", False),
        ("parent_id", 42),
        ("thread_id", []),
        ("sender", {"id": "cli_fake", "id_type": "open_id", "sender_type": "user"}),
    ],
)
def test_optional_ids_and_sender_shape_are_strict(env, field, value):
    e = env
    share(e)
    e.items[0][field] = value
    with pytest.raises(DiscoveryError, match="PROTOCOL"):
        e.read()
