import httpx
import pytest
from engine.feishu_conversation_store import (
    ConversationStore,
    SendingError,
    RETRY_WINDOW,
    render_text,
)
from engine.feishu_conversation_worker import ConversationWorker
from engine.feishu_client import SendFailure, SharedFeishuBackupTransport
from tests.test_feishu_conversation_sending import accept, IDENTITY, Wire
from tests.test_feishu_conversations import client


def test_rollback_catchup_never_rearms(tmp_path):
    now = [100.0]
    store = ConversationStore(tmp_path / "q", clock=lambda: now[0])
    accept(store)
    wire = Wire()
    wire.response = RuntimeError()
    with ConversationWorker(store, wire, authorize=lambda _: True) as worker:
        worker.run_once()
        now[0] = 200
        worker.run_once()
        now[0] = 180
        worker.run_once()
        now[0] = 400
        worker.run_once()
        assert len(wire.calls) == 2
        assert accept(store)["final"] is True
        assert accept(store)["reason"] == "CLOCK_UNTRUSTED"
    store.close()


@pytest.mark.parametrize("delay", [1, 4000])
@pytest.mark.parametrize("status", ["sending", "unknown", "pending"])
def test_restart_retires_attempted(tmp_path, delay, status):
    now = [100.0]
    store = ConversationStore(tmp_path / "q", clock=lambda: now[0])
    accept(store)
    job = store.next_job()
    store.begin(job)
    if status != "sending":
        store.update(job, status, "SEND_BACKOFF")
    store.close()
    now[0] += delay
    store = ConversationStore(tmp_path / "q", clock=lambda: now[0])
    wire = Wire()
    with ConversationWorker(store, wire, authorize=lambda _: True) as worker:
        worker.run_once()
    receipt = accept(store)
    assert wire.calls == [] and receipt["final"]
    assert receipt["status"] == ("failed" if status == "pending" else "unknown")
    store.close()


@pytest.mark.parametrize("jump", ["monotonic", "rollback", "forward"])
def test_monotonic_cutoff_after_prepare_no_extra_post(tmp_path, jump):
    wall, mono = [100.0], [0.0]
    store = ConversationStore(tmp_path / "q", clock=lambda: wall[0], monotonic=lambda: mono[0])
    accept(store)
    calls = []
    api = client(lambda req: calls.append(req) or httpx.Response(500), clock=lambda: wall[0])
    with ConversationWorker(store, api, authorize=lambda _: True) as worker:
        worker.run_once()
        wall[0] += 61
        original = api._token

        def slow_token(*args):
            if jump == "monotonic":
                mono[0] = RETRY_WINDOW + 1
            elif jump == "rollback":
                wall[0] -= 1
            else:
                wall[0] += RETRY_WINDOW
            return original(*args)

        api._token = slow_token
        worker.run_once()
        assert len(calls) == 1 and accept(store)["final"]
    api.close()
    store.close()


@pytest.mark.parametrize(
    "code,kind",
    [
        (230002, "membership"),
        (232009, "membership"),
        *[
            (c, "failed")
            for c in (
                230001,
                230006,
                230013,
                230015,
                230017,
                230018,
                230019,
                230022,
                230025,
                230027,
                230099,
                230028,
                230029,
                230034,
                230035,
                230036,
                230038,
                230053,
                230054,
                230055,
                230075,
            )
        ],
        (230020, "backoff"),
        (99991661, "backoff"),
        (99991663, "backoff"),
        (230049, "uncertain"),
        (876543, "uncertain"),
    ],
)
def test_actual_http400_codes_and_backup_adapter(code, kind):
    api = client(
        lambda _: httpx.Response(400, json={"code": code, "msg": "synthetic private body"})
    )
    with pytest.raises(SendFailure) as caught:
        api.send_text("oc_one", "synthetic", "stable")
    assert caught.value.kind == kind
    assert caught.value.__context__ is None
    assert "body" not in str(caught.value)
    api.close()
    api = client(lambda _: httpx.Response(400, json={"code": code}))
    expected = "unknown" if kind == "uncertain" else "not_sent"
    assert SharedFeishuBackupTransport(api).send(
        dict(chat="oc_one", rendered="synthetic", delivery_uuid="stable")
    ) == (expected, None)
    api.close()


def test_bounds_replay_target_fairness(tmp_path):
    store = ConversationStore(
        tmp_path / "q", principal_active_limit=2, principal_total_limit=3, global_total_limit=5
    )
    accept(store, "a")
    accept(store, "b")
    assert accept(store, "a")["eventId"] == "a"
    with pytest.raises(SendingError, match="QUEUE_FULL"):
        accept(store, "c")
    with pytest.raises(SendingError, match="CONFLICT"):
        accept(store, "a", target="oc_changed")
    accept(store, "x", identity={**IDENTITY, "user_id": "bob"})
    assert store.next_job()["user_id"] == "alice"
    assert store.next_job()["user_id"] == "bob"
    store.close()


def test_quarantine_and_stale(tmp_path):
    now = [100.0]
    store = ConversationStore(tmp_path / "q", clock=lambda: now[0])
    accept(store, target="oc_one")
    job = store.next_job()
    store.begin(job)
    store.complete(job, {"message_id": "om_one", "chat_id": "oc_wrong"})
    assert accept(store, target="oc_one")["final"]
    now[0] += 61
    assert store.next_job() is None
    accept(store, "stale")
    now[0] += 86401
    assert store.next_job() is None
    assert accept(store, "stale")["status"] == "failed"
    store.close()


def test_body_quotes_and_multiline_preserved_controls_inert():
    value = render_text("Alice", "【quoted】\ncode\t&\u202e")
    assert "【quoted】\ncode\t&" in value and "\u202e" not in value


def test_no_wire_preparation_does_not_start_window(tmp_path):
    now = [100.0]
    store = ConversationStore(tmp_path / "q", clock=lambda: now[0])
    accept(store)
    api = client(lambda _: pytest.fail("no HTTP allowed"))
    api._token = lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic"))
    with ConversationWorker(store, api, authorize=lambda _: True) as worker:
        for _ in range(10):
            worker.run_once()
            now[0] += 301
        row = dict(store.db.execute("SELECT * FROM submissions").fetchone())
        assert row["attempts"] == 0 and row["first_attempt"] is None
        assert row["status"] == "failed" and row["final"]
    store.close()
    api.close()


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (401, None, "backoff"),
        (500, 230002, "uncertain"),
        (403, None, "uncertain"),
        (200, 230049, "uncertain"),
    ],
)
def test_status_does_not_override_uncertainty(status, code, expected):
    api = client(lambda _: httpx.Response(status, json={"code": code}))
    with pytest.raises(SendFailure) as caught:
        api.send_text("oc_one", "synthetic", "stable")
    assert caught.value.kind == expected
    if status == 401:
        assert api.token is None
    api.close()


@pytest.mark.parametrize("kind", ["membership", "failed"])
def test_prior_unknown_definitive_rejection_is_final_unknown(tmp_path, kind):
    now = [100.0]
    store = ConversationStore(tmp_path / "q", clock=lambda: now[0])
    accept(store)
    wire = Wire()
    wire.response = RuntimeError()
    with ConversationWorker(store, wire, authorize=lambda _: True) as worker:
        worker.run_once()
        now[0] += 61
        wire.response = SendFailure(kind=kind)
        worker.run_once()
        assert accept(store)["status"] == "unknown" and accept(store)["final"]
        now[0] += 301
        worker.run_once()
        assert len(wire.calls) == 2
    store.close()


@pytest.mark.parametrize(
    "limit",
    [
        "principal_total_limit",
        "global_total_limit",
        "principal_byte_limit",
        "global_byte_limit",
        "global_active_limit",
    ],
)
def test_total_and_byte_capacity_retains_originals(tmp_path, limit):
    value = 1 if "byte" not in limit else 5000
    store = ConversationStore(tmp_path / "q", **{limit: value})
    accept(store)
    if "active" not in limit:
        store.complete(store.next_job(), {"message_id": "om_one", "chat_id": "oc_one"})
    with pytest.raises(SendingError, match="QUEUE_FULL"):
        accept(store, "two")
    assert accept(store)["eventId"] == "event-1"
    assert store.db.execute("SELECT text FROM submissions").fetchone()[0] == "hello"
    store.close()


@pytest.mark.parametrize(
    "metadata",
    [
        {"external": None, "tenant_key": "team"},
        {"external": False},
        {"external": "false", "tenant_key": "team"},
    ],
)
def test_malformed_metadata_protocol_error(tmp_path, metadata):
    from engine.feishu_conversations import ConversationDiscovery, ConversationMappings
    from engine.feishu_client import DiscoveryError

    api = client(lambda _: httpx.Response(500))
    mapping = ConversationMappings(tmp_path / "map")
    discovery = ConversationDiscovery(api, mapping)
    with pytest.raises(DiscoveryError, match="UPSTREAM_PROTOCOL_ERROR"):
        discovery._validated_chats(
            [dict(chat_id="oc_one", name="Synthetic", **metadata)], strict_eligibility=True
        )
    mapping.close()
    api.close()


def test_receipt_binding_quarantine(tmp_path):
    store = ConversationStore(tmp_path / "q")
    accept(store)
    store.complete(store.next_job(), {"message_id": "om_one", "chat_id": "oc_one"})
    accept(store, "two")
    job = store.next_job()
    store.begin(job)
    store.complete(job, {"message_id": "om_two", "chat_id": "oc_other"})
    assert accept(store, "two")["reason"] == "RECEIPT_MISMATCH"
    assert accept(store, "two")["final"] and store.next_job() is None
    store.close()


# Real routes/lifecycle with only the existing fake tenant and synthetic login fixture.
from tests.test_feishu_conversation_sending import sending_api as sending_api  # noqa: E402


def test_api_capacity_replay_and_health(sending_api):
    context, http, headers, conversations, _ = sending_api
    context.conversation_store.limits = (1, 2, 2, 3, 100000, 200000)
    path = f"/v1/conversations/{conversations[0]['id']}/messages"
    payload = {"eventId": "bound", "text": "synthetic"}
    assert http.post(path, headers=headers, json=payload).status_code == 202
    assert http.post(path, headers=headers, json=payload).status_code == 202
    full = http.post(path, headers=headers, json={**payload, "eventId": "second"})
    assert full.status_code == 429 and full.json() == {"detail": "QUEUE_FULL"}
    http.app.state.conversation_runtime.failed.set()
    assert http.get("/healthz").status_code == 503


@pytest.mark.parametrize(
    "code,status",
    [
        ("RENDERED_MESSAGE_TOO_LARGE", 413),
        ("EVENT_PAYLOAD_CONFLICT", 409),
        ("QUEUE_FULL", 429),
        ("PRIVATE_STORAGE_REQUIRED", 503),
    ],
)
def test_precise_error_status_map(sending_api, monkeypatch, code, status):
    context, http, headers, conversations, _ = sending_api

    def fail(*args):
        raise SendingError(code)

    monkeypatch.setattr(context.conversation_store, "accept", fail)
    response = http.post(
        f"/v1/conversations/{conversations[0]['id']}/messages",
        headers=headers,
        json={"eventId": "test", "text": "synthetic"},
    )
    assert response.status_code == status and response.json() == {"detail": code}
