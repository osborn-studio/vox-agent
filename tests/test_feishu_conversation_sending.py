"""Phase 4 offline tests: private SSD state and injected wire/identity effects only."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from engine.feishu_conversation_store import (
    ConversationStore,
    SendInput,
    SendingError,
    render_text,
    MAX_BYTES,
)
from engine.feishu_conversation_worker import ConversationWorker


IDENTITY = dict(
    app_id="cli_test",
    tenant="team",
    org_id="org",
    user_id="alice",
    open_id="ou_alice",
    name="Alice",
)


def accept(store, event="event-1", text="hello", conversation="fc_dm", target="dm", identity=None):
    return store.accept(
        identity or IDENTITY, conversation, target, SendInput(eventId=event, text=text)
    )


def test_durable_idempotency_conflict_current_receipt_and_scope(tmp_path):
    path = tmp_path / "outbox.sqlite"
    store = ConversationStore(path)
    first = accept(store)
    assert first["status"] == "pending"
    with ThreadPoolExecutor(8) as pool:
        assert all(x == first for x in pool.map(lambda _: accept(store), range(16)))
    for kwargs in [dict(text="changed"), dict(conversation="fc_group", target="oc_group")]:
        with pytest.raises(SendingError, match="EVENT_PAYLOAD_CONFLICT"):
            accept(store, **kwargs)
    assert store.receipt(IDENTITY, "fc_other", "event-1") is None
    assert store.receipt({**IDENTITY, "org_id": "other"}, "fc_dm", "event-1") is None
    store.close()
    store = ConversationStore(path)
    assert accept(store) == first
    assert path.stat().st_mode & 0o077 == 0
    store.close()


def test_rendering_strict_input_and_utf8_boundary():
    value = render_text("\n【Eve】\u202e<at>" * 20, '<at user_id="all">@all</at>')
    assert "<at" not in value and "@" not in value and "\n" not in value and "\u202e" not in value
    prefix = render_text("A", "")
    assert len(render_text("A", "a" * (MAX_BYTES - len(prefix.encode()))).encode()) == MAX_BYTES
    with pytest.raises(SendingError, match="RENDERED_MESSAGE_TOO_LARGE"):
        render_text("A", "a" * (MAX_BYTES - len(prefix.encode())) + "中")
    for body in [
        dict(eventId="x", text="x", author="forged"),
        dict(eventId=True, text="x"),
        dict(eventId="x", text="  "),
        dict(eventId="x", text="\ud800"),
    ]:
        with pytest.raises(ValueError):
            SendInput(**body)


class Wire:
    def __init__(self):
        self.calls = []
        self.response = {"message_id": "om_one", "chat_id": "oc_dm"}

    def send_text(self, target, text, uuid):
        self.calls.append((target, text, uuid))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_worker_recovery_retry_uuid_window_and_no_uncertainty_downgrade(tmp_path):
    now = [100.0]
    store = ConversationStore(tmp_path / "out.sqlite", clock=lambda: now[0])
    accept(store)
    wire = Wire()
    wire.response = RuntimeError("private transcript secret")
    with ConversationWorker(store, wire, authorize=lambda job: True) as worker:
        worker.run_once()
        assert accept(store)["status"] == "unknown"
        now[0] += 61
        worker.authorize = lambda job: False
        worker.run_once()
        assert accept(store)["status"] == "unknown"
        worker.authorize = lambda job: True
        now[0] += 121
        wire.response = {"message_id": "om_one", "chat_id": "oc_dm"}
        worker.run_once()
        assert accept(store)["status"] == "unknown"
        assert accept(store)["final"] is True
        assert len(wire.calls) == 1  # Definitive authorization loss permanently stops retries.
        assert len({c[2] for c in wire.calls}) == 1
    accept(store, event="crash")
    store.begin(store.next_job())
    store.close()
    store = ConversationStore(tmp_path / "out.sqlite", clock=lambda: now[0])
    now[0] += 3601
    with ConversationWorker(store, wire, authorize=lambda job: True) as worker:
        assert store.receipt(IDENTITY, "fc_dm", "crash")["status"] == "unknown"
        worker.run_once()
    assert store.receipt(IDENTITY, "fc_dm", "crash")["status"] == "unknown"
    assert len(wire.calls) == 1
    store.close()


def test_definitive_loss_and_preflight_unavailable_are_distinct(tmp_path):
    from engine.feishu_client import DiscoveryError

    store = ConversationStore(tmp_path / "out.sqlite")
    wire = Wire()
    accept(store)

    def unavailable(job):
        raise DiscoveryError("UPSTREAM_UNAVAILABLE")

    with ConversationWorker(store, wire, authorize=unavailable) as worker:
        worker.run_once()
        assert accept(store)["status"] == "pending"
        assert accept(store)["reason"] == "PREFLIGHT_UNAVAILABLE"
        accept(store, event="revoked")
        worker.authorize = lambda job: False
        worker.run_once()
        assert accept(store, event="revoked")["status"] == "membership_lost"
    assert wire.calls == []
    store.close()


def test_atomic_dm_binding_conflict_and_single_worker_lock(tmp_path):
    store = ConversationStore(tmp_path / "out.sqlite")
    wire = Wire()
    accept(store)
    with ConversationWorker(store, wire, authorize=lambda job: True) as worker:
        with pytest.raises(SendingError, match="WORKER_ALREADY_RUNNING"):
            with ConversationWorker(store, wire, authorize=lambda job: True):
                pass
        worker.run_once()
        assert accept(store)["messageId"] == "om_one"
        accept(store, event="second")
        wire.response = {"message_id": "om_two", "chat_id": "oc_conflict"}
        worker.run_once()
        receipt = accept(store, event="second")
        assert receipt["status"] == "unknown" and "messageId" not in receipt
    store.close()


@pytest.fixture
def sending_api(tmp_path):
    import httpx
    from fastapi.testclient import TestClient
    from engine.backup_runtime import create_runtime_app
    from tests.test_backup_runtime import settings, secret
    from tests.test_feishu_login import Provider, login
    from tests.test_feishu_conversations import page, chat

    state = dict(calls=[], members=["ou_alice"], external=False, fail=False)

    def handle(req):
        state["calls"].append(req)
        if req.url.path.endswith("/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "synthetic", "expire": 7200}
            )
        if req.method == "POST":
            return httpx.Response(
                200, json={"code": 0, "data": {"message_id": "om_sent", "chat_id": "oc_one"}}
            )
        if state["fail"]:
            return httpx.Response(500, text="sensitive body")
        if req.url.path.endswith("/chats"):
            return httpx.Response(200, json=page([chat(external=state["external"])]))
        return httpx.Response(200, json=page([{"member_id": m} for m in state["members"]]))

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
        conversation_transport_factory=lambda: httpx.MockTransport(handle),
    )
    with TestClient(app, base_url="http://127.0.0.1:8766") as http:
        app.state.conversation_runtime.close()  # deterministic manual worker stepping
        assert state["calls"] == []
        svc = app.state.feishu_login
        svc.provider = Provider()
        result = login(svc)
        headers = {"Authorization": "Bearer " + result["token"]}
        discovered = http.get("/v1/conversations", headers=headers).json()["conversations"]
        yield app.state.context, http, headers, discovered, state


def test_api_strict_scope_idempotency_freezes_author_and_no_body_leak(sending_api):
    context, http, headers, conversations, state = sending_api
    from engine.feishu_conversation_worker import authorize_current
    from engine.auth import Principal

    assert {c["capability"] for c in conversations} == {"text_send"}
    dm, group = [c["id"] for c in conversations]

    def submit(cid=dm, **body):
        return http.post(
            f"/v1/conversations/{cid}/messages",
            headers=headers,
            json={"eventId": "one", "text": "private transcript", **body},
        )

    for extra in [
        dict(name="fake"),
        dict(target="oc_one"),
        dict(userId="alice"),
        dict(eventId=True),
    ]:
        response = submit(**extra)
        assert (
            response.status_code == 422
            and "private" not in response.text
            and "fake" not in response.text
        )
    assert submit("fc_forged").status_code == 403
    first = submit()
    assert first.status_code == 202 and first.json()["status"] == "pending"
    assert first.headers["cache-control"] == "no-store"
    with ThreadPoolExecutor(8) as pool:
        replies = list(pool.map(lambda _: submit().json(), range(12)))
    assert all(r == first.json() for r in replies)
    assert submit(group).status_code == 409
    assert submit(text="different").status_code == 409
    assert http.get(f"/v1/conversations/{group}/receipts/one", headers=headers).status_code == 404
    svc = context.auth_service.feishu_login
    user = svc.db.execute("SELECT user_id FROM feishu_identity").fetchone()[0]
    identity = svc.conversation_identity(Principal(user, "default-org", "member", "feishu"))
    foreign = context.conversation_discovery.mappings.identifiers(
        {**identity, "user_id": "other"}, ["oc_one"]
    )[0]
    assert submit(foreign).status_code == 403
    with svc.db:
        svc.db.execute("UPDATE feishu_identity SET name='Changed'")
    with ConversationWorker(
        context.conversation_store,
        context.conversation_discovery.client,
        authorize=lambda job: authorize_current(context, job),
    ) as worker:
        worker.run_once()
    assert submit().json()["status"] == "sent"
    import json

    sends = [r for r in state["calls"] if r.url.path.endswith("/messages")]
    assert len(sends) == 1
    assert (
        json.loads(json.loads(sends[0].content)["content"])["text"] == "【Alice】private transcript"
    )
    assert context.backup_store.db.execute("SELECT count(*) FROM backup_outbox").fetchone()[0] == 0


@pytest.mark.parametrize(
    "change,expected",
    [
        ("removed", "membership_lost"),
        ("external", "membership_lost"),
        ("denied", "membership_lost"),
        ("disabled", "membership_lost"),
        ("app", "membership_lost"),
        ("tenant", "membership_lost"),
        ("open_id", "membership_lost"),
        ("unavailable", "pending"),
    ],
)
def test_worker_fresh_preflight_ignores_discovery_cache(sending_api, change, expected):
    from engine.backup_app import ConversationPolicy
    from engine.feishu_conversation_worker import authorize_current

    context, http, headers, conversations, state = sending_api
    group = conversations[1]["id"]
    response = http.post(
        f"/v1/conversations/{group}/messages",
        headers=headers,
        json={"eventId": "fresh", "text": "synthetic"},
    )
    assert response.status_code == 202
    if change == "removed":
        state["members"] = []
    if change == "external":
        state["external"] = True
    if change == "unavailable":
        state["fail"] = True
    if change == "denied":
        context.conversation_policy = ConversationPolicy(denied_chat_ids=["oc_one"])
    svc = context.auth_service.feishu_login
    if change == "disabled":
        user = svc.db.execute("SELECT user_id FROM feishu_identity").fetchone()[0]
        svc.auth.org_store.update_user(user, status="disabled")
    if change in ("app", "tenant", "open_id"):
        column = {"app": "app_id", "tenant": "tenant", "open_id": "open_id"}[change]
        with svc.db:
            svc.db.execute(f"UPDATE feishu_identity SET {column}='other'")
    with ConversationWorker(
        context.conversation_store,
        context.conversation_discovery.client,
        authorize=lambda job: authorize_current(context, job),
    ) as worker:
        worker.run_once()
    row = context.conversation_store.db.execute("SELECT status FROM submissions").fetchone()
    assert row[0] == expected
    assert not any(r.url.path.endswith("/messages") for r in state["calls"])


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"message_id": "om_one"},
        {"message_id": True, "chat_id": "oc_one"},
        {"message_id": "om_one", "chat_id": "oc_other"},
    ],
)
def test_invalid_or_wrong_group_receipt_is_unknown(tmp_path, response):
    store = ConversationStore(tmp_path / "out.sqlite")
    accept(store, target="oc_one")
    wire = Wire()
    wire.response = response
    with ConversationWorker(store, wire, authorize=lambda _: True) as worker:
        worker.run_once()
    assert accept(store, target="oc_one")["status"] == "unknown"
    store.close()


def test_send_transport_uuid_content_rate_and_safe_failures():
    import httpx
    import json
    from engine.feishu_client import SendFailure
    from tests.test_feishu_conversations import client

    calls = []
    api = client(
        lambda req: (
            calls.append(req)
            or httpx.Response(
                200, json={"code": 0, "data": {"message_id": "om_one", "chat_id": "oc_one"}}
            )
        ),
        clock=lambda: 0,
    )
    for _ in range(5):
        api.send_text("oc_one", "【Alice】&lt;at&gt;", "stable")
    with pytest.raises(SendFailure):
        api.send_text("oc_one", "same", "stable")
    assert len(calls) == 5
    assert all(json.loads(r.content)["uuid"] == "stable" for r in calls)
    api.close()
    for response in [
        httpx.Response(429, text="secret"),
        httpx.Response(500, text="private"),
        httpx.Response(403, text="secret"),
        httpx.Response(200, json={"code": 230020}),
    ]:
        api = client(lambda _: response)
        with pytest.raises(SendFailure) as exc:
            api.send_text("ou_alice", "private", "same")
        assert "private" not in str(exc.value) and "secret" not in str(exc.value)
        assert exc.value.__context__ is None
        api.close()


def test_concurrent_changed_events_have_one_winner(tmp_path):
    store = ConversationStore(tmp_path / "out.sqlite")

    def submit(i):
        try:
            return accept(store, text=str(i))
        except SendingError:
            return None

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(submit, range(16)))
    assert sum(r is not None for r in results) == 1
    assert store.db.execute("SELECT count(*) FROM submissions").fetchone()[0] == 1
    store.close()


def test_retry_cutoff_rechecked_after_preflight_and_exhaustion(tmp_path):
    from engine.feishu_conversation_store import RETRY_WINDOW, MAX_ATTEMPTS

    now = [100.0]
    store = ConversationStore(tmp_path / "out.sqlite", clock=lambda: now[0])
    accept(store)
    wire = Wire()
    wire.response = RuntimeError("uncertain")
    with ConversationWorker(store, wire, authorize=lambda _: True) as worker:
        worker.run_once()
        now[0] += 61

        def slow_preflight(_):
            now[0] = 100 + RETRY_WINDOW
            return True

        worker.authorize = slow_preflight
        worker.run_once()
        assert len(wire.calls) == 1
        assert accept(store)["status"] == "unknown"
        accept(store, event="bounded")
        worker.authorize = lambda _: True
        for _ in range(MAX_ATTEMPTS + 3):
            worker.run_once()
            now[0] += 301
        assert len(wire.calls) == 1 + MAX_ATTEMPTS
    store.close()


def test_organization_remapping_never_reuses_old_id(tmp_path):
    from engine.feishu_conversations import ConversationMappings

    mapping = ConversationMappings(tmp_path / "ids.sqlite")
    first = mapping.identifiers(IDENTITY, ["dm", "oc_one"])
    other = {**IDENTITY, "org_id": "other"}
    second = mapping.identifiers(other, ["dm", "oc_one"])
    assert not set(first) & set(second)
    assert mapping.resolve(other, first[1]) is None
    assert mapping.resolve(IDENTITY, first[1]) == "oc_one"
    mapping.close()


def test_concurrent_first_dm_binding_persists_both_receipts(tmp_path):
    store = ConversationStore(tmp_path / "out.sqlite")
    accept(store, event="one")
    accept(store, event="two")
    jobs = [dict(r) for r in store.db.execute("SELECT * FROM submissions")]
    with ThreadPoolExecutor(2) as pool:
        list(
            pool.map(
                lambda job: store.complete(
                    job, {"message_id": "om_" + job["event"], "chat_id": "oc_same"}
                ),
                jobs,
            )
        )
    assert store.db.execute("SELECT count(*) FROM dm_bindings").fetchone()[0] == 1
    assert {r[0] for r in store.db.execute("SELECT status FROM submissions")} == {"sent"}
    store.close()


def test_preflight_retry_limit_finishes_failed_not_revoked(tmp_path):
    now = [100.0]
    store = ConversationStore(tmp_path / "out.sqlite", clock=lambda: now[0])
    accept(store)

    def unavailable(_):
        raise RuntimeError("private upstream failure")

    wire = Wire()
    with ConversationWorker(store, wire, authorize=unavailable) as worker:
        for _ in range(12):
            worker.run_once()
            now[0] += 301
    assert accept(store)["status"] == "failed"
    assert accept(store)["reason"] == "RETRY_EXHAUSTED"
    assert wire.calls == []
    store.close()


def test_author_unicode_line_separators_are_inert():
    rendered = render_text("A\u2028B\u2029C", "hello")
    assert "\u2028" not in rendered and "\u2029" not in rendered


def test_complete_fresh_member_traversal_required_even_if_first_page_matches(tmp_path):
    from types import SimpleNamespace
    import httpx
    from engine.feishu_conversations import ConversationMappings, ConversationDiscovery
    from engine.feishu_conversation_worker import authorize_current
    from engine.backup_app import ConversationPolicy
    from tests.test_feishu_conversations import client, page, chat

    requests = []

    def handle(req):
        requests.append(req)
        if req.url.path.endswith("/chats"):
            return httpx.Response(200, json=page([chat()]))
        if "page_token" not in req.url.params:
            return httpx.Response(
                200, json=page([{"member_id": "ou_alice"}], has_more=True, page_token="next")
            )
        return httpx.Response(500, text="sensitive failure")

    api = client(handle)
    mapping = ConversationMappings(tmp_path / "ids.sqlite")
    cid = mapping.identifiers(IDENTITY, ["oc_one"])[0]
    context = SimpleNamespace(
        conversations_enabled=True,
        conversation_policy=ConversationPolicy(),
        conversation_discovery=ConversationDiscovery(api, mapping),
        auth_service=SimpleNamespace(
            feishu_login=SimpleNamespace(conversation_identity=lambda _: IDENTITY)
        ),
    )
    store = ConversationStore(tmp_path / "out.sqlite")
    accept(store, conversation=cid, target="oc_one")
    with ConversationWorker(
        store, api, authorize=lambda job: authorize_current(context, job)
    ) as worker:
        worker.run_once()
    assert accept(store, conversation=cid, target="oc_one")["reason"] == "PREFLIGHT_UNAVAILABLE"
    assert len(requests) == 3 and all(r.method == "GET" for r in requests)
    store.close()
    mapping.close()
    api.close()


def test_expired_session_cannot_submit_or_read_receipt(sending_api):
    context, http, headers, conversations, _ = sending_api
    cid = conversations[0]["id"]
    assert (
        http.post(
            f"/v1/conversations/{cid}/messages",
            headers=headers,
            json={"eventId": "one", "text": "synthetic"},
        ).status_code
        == 202
    )
    context.auth_service.feishu_login.logout(headers["Authorization"].removeprefix("Bearer "))
    assert (
        http.post(
            f"/v1/conversations/{cid}/messages",
            headers=headers,
            json={"eventId": "two", "text": "synthetic"},
        ).status_code
        == 401
    )
    assert http.get(f"/v1/conversations/{cid}/receipts/one", headers=headers).status_code == 401
    assert (
        context.conversation_store.db.execute("SELECT count(*) FROM submissions").fetchone()[0] == 1
    )


def test_definitive_send_rejection_never_overwrites_prior_ambiguity(tmp_path):
    from engine.feishu_client import SendFailure

    now = [100.0]
    store = ConversationStore(tmp_path / "out.sqlite", clock=lambda: now[0])
    accept(store)
    wire = Wire()
    wire.response = RuntimeError("uncertain")
    with ConversationWorker(store, wire, authorize=lambda _: True) as worker:
        worker.run_once()
        now[0] += 61
        wire.response = SendFailure(definitive=True)
        worker.run_once()
        assert accept(store)["status"] == "unknown"
    store.close()


def test_live_sending_receipt_reports_current_durable_state(tmp_path):
    store = ConversationStore(tmp_path / "out.sqlite")
    accept(store)
    store.begin(store.next_job())
    assert accept(store)["status"] == "sending"
    store.close()


def test_backup_and_conversation_share_recipient_and_app_budget():
    import httpx
    from engine.feishu_client import SharedFeishuBackupTransport
    from tests.test_feishu_conversations import client

    calls = []
    api = client(
        lambda req: (
            calls.append(req)
            or httpx.Response(
                200, json={"code": 0, "data": {"message_id": "om_one", "chat_id": "oc_one"}}
            )
        ),
        clock=lambda: 0,
    )
    backup = SharedFeishuBackupTransport(api)
    for _ in range(4):
        api.send_text("oc_one", "conversation", "one")
    job = {"chat": "oc_one", "rendered": "backup", "delivery_uuid": "other"}
    assert backup.send(job) == ("delivered", "om_one")
    assert backup.send(job) == ("not_sent", None)
    assert len(calls) == 5
    api.close()


def test_runtime_idle_and_shutdown_wait_for_inflight_before_store_close(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    from engine.feishu_conversation_worker import ConversationRuntime
    import engine.feishu_conversation_worker as worker_module

    entered, release = threading.Event(), threading.Event()

    class BlockingWire(Wire):
        def send_text(self, target, text, uuid):
            entered.set()
            assert release.wait(2)
            return super().send_text(target, text, uuid)

    store = ConversationStore(tmp_path / "out.sqlite")
    wire = BlockingWire()
    context = SimpleNamespace(
        conversation_store=store, conversation_discovery=SimpleNamespace(client=wire)
    )
    monkeypatch.setattr(worker_module, "authorize_current", lambda context, job: True)
    runtime = ConversationRuntime()
    runtime.start(context)
    assert wire.calls == []
    accept(store)
    assert entered.wait(2)
    closed = threading.Event()
    closer = threading.Thread(target=lambda: (runtime.close(), closed.set()))
    closer.start()
    assert not closed.wait(0.05)
    release.set()
    closer.join(2)
    assert closed.is_set() and not runtime.thread.is_alive()
    assert accept(store)["status"] == "sent"
    store.close()
