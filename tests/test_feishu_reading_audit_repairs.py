"""Regression probes from the independent Phase 5 audit, synthetic transport only."""

from types import MethodType

import pytest

from engine.feishu_client import DiscoveryError, FeishuClient
from engine.feishu_conversation_reading import ReadingError
from tests.test_feishu_conversation_reading import (
    env as reading_environment,
    message,
    original,
    share,
)
from tests.test_feishu_conversation_sending import IDENTITY


@pytest.fixture
def env(tmp_path):
    yield from reading_environment.__wrapped__(tmp_path)


def bind_dm(e):
    original(e, target="dm", conversation=e.dm)
    e.store.complete(e.store.next_job(), {"message_id": "om_one", "chat_id": "oc_dm"})
    e.items[0]["chat_id"] = "oc_dm"
    e.items[0]["sender"] = {"id": IDENTITY["app_id"], "id_type": "app_id", "sender_type": "app"}


def test_changed_open_id_cannot_read_old_dm(env):
    bind_dm(env)
    env.identity = {**IDENTITY, "open_id": "ou_changed"}
    result = env.read(env.dm)
    assert result["mode"] == "own_submissions"
    assert not any(r.url.path.endswith("/messages") for r in env.calls)


def test_dm_rejects_third_party_sender(env):
    bind_dm(env)
    env.items[0]["sender"] = message()["sender"]
    with pytest.raises(DiscoveryError, match="UPSTREAM_PROTOCOL_ERROR"):
        env.read(env.dm)


def test_user_sender_cannot_borrow_stored_vox_attribution(env):
    share(env)
    original(env)
    env.store.complete(env.store.next_job(), {"message_id": "om_one", "chat_id": "oc_group"})
    item = env.read()["items"][0]
    assert item["authorKind"] == "feishu_user" and item["authorName"] is None


def test_latest_poll_cursor_pressure_never_denies_fresh_reads(env):
    share(env)
    env.more = True
    first = env.read()["nextCursor"]
    for _ in range(600):
        assert env.read()["nextCursor"]
    assert len(env.reading.cursors) <= 128
    with pytest.raises(ReadingError, match="INVALID_CURSOR"):
        env.read(cursor=first)


def test_history_snapshot_excludes_open_second_and_stays_fixed(env):
    share(env)
    env.now = 1001.5
    env.more = True
    env.items[0].update(create_time="1001600", update_time="1001600")
    first = env.read()
    assert first["items"] == []
    env.now = 1003.5
    env.token = "different"
    env.items[0].update(create_time="1002000", update_time="1002000")
    assert env.read(cursor=first["nextCursor"])["items"] == []
    requests = [r for r in env.calls if r.url.path.endswith("/messages")]
    assert {r.url.params["end_time"] for r in requests} == {"1001"}


def test_read_requests_leave_local_send_budget(env):
    share(env)
    api = env.ctx.conversation_discovery.client
    api._reserve = MethodType(FeishuClient._reserve, api)
    api.clock = lambda: env.now
    # Five wire calls per read; 150 read calls must leave half of app capacity.
    for _ in range(30):
        env.read()
        env.now += 0.6
    with pytest.raises(DiscoveryError, match="RATE_LIMITED"):
        env.read()
    receipt = api.send_text("oc_group", "synthetic quota probe", "quota-probe")
    assert receipt == {"message_id": "om_sent", "chat_id": "oc_group"}
    assert len(api.windows[("read", api.app_id)]) == 150
    assert len(api.windows[("app", api.app_id)]) == 152  # read + bootstrap + send


@pytest.mark.parametrize(
    "kind",
    ["location", "todo", "vote", "hongbao", "share_calendar_event", "calendar", "general_calendar"],
)
def test_documented_nontext_messages_are_inert_placeholders(env, kind):
    share(env)
    env.items = [message(), {**message("om_other"), "msg_type": kind}]
    result = env.read()["items"]
    assert result[0]["text"] is not None
    assert result[1]["placeholder"] and result[1]["text"] is None


@pytest.mark.parametrize(
    "text", ["x" * 30_000, "x" * 110_000, "private\x00body"], ids=["30kb", "110kb", "nul"]
)
def test_text_outside_display_bounds_does_not_poison_whole_page(env, text):
    import json

    share(env)
    env.items = [message(), {**message("om_long"), "body": {"content": json.dumps({"text": text})}}]
    result = env.read()["items"]
    assert result[0]["text"] is not None
    assert result[1]["placeholder"] and result[1]["text"] is None


def test_unknown_system_sender_is_an_authorless_placeholder_in_bound_dm(env):
    bind_dm(env)
    env.items[0].update(msg_type="system", sender={"id": "", "sender_type": "unknown"})
    item = env.read(env.dm)["items"][0]
    assert item["placeholder"] and item["text"] is None
    assert item["authorName"] is None and item["authorId"] == ""
    env.items[0]["msg_type"] = "text"
    with pytest.raises(DiscoveryError, match="UPSTREAM_PROTOCOL_ERROR"):
        env.read(env.dm)
