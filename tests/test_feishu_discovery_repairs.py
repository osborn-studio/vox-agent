"""Offline regression evidence for second-loop bounded discovery repairs."""

import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from engine.backup_app import ConversationPolicy
from engine.feishu_client import DiscoveryError
from engine.feishu_conversations import ConversationDiscovery, ConversationMappings
from tests.test_feishu_conversations import chat, client, page

IDENTITY = dict(app_id="cli_test", tenant="team", user_id="alice", open_id="ou_alice")


@pytest.mark.parametrize(
    "code,category",
    [(99991400, "RATE_LIMITED"), (99991661, "PERMISSION_DENIED"), (99991663, "PERMISSION_DENIED")],
)
def test_defensive_400_business_codes(code, category):
    calls = []
    api = client(
        lambda r: calls.append(r) or httpx.Response(400, json={"code": code, "msg": "secret"})
    )
    with pytest.raises(DiscoveryError, match=category):
        api.chats()
    if code == 99991400:
        with pytest.raises(DiscoveryError, match=category):
            api.chats()
        assert len(calls) == 1
    else:
        assert api.token is None


def test_transport_failure_has_static_unavailable_without_chain():
    def fail(req):
        raise httpx.ReadTimeout("secret", request=req)

    with pytest.raises(DiscoveryError, match="UPSTREAM_UNAVAILABLE") as error:
        client(fail).chats()
    assert error.value.__context__ is None


def test_identity_encoding_and_raw_limit():
    encodings = []

    def handle(req):
        encodings.append(req.headers["accept-encoding"])
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=b"invalid")

    with pytest.raises(DiscoveryError, match="UPSTREAM_PROTOCOL_ERROR"):
        client(handle).chats()
    assert encodings == ["identity"]


def test_chat_grammar_matches_policy():
    for cid in ["oc_a-b", "oc_a_b"]:
        with pytest.raises(ValueError):
            ConversationPolicy(denied_chat_ids=[cid])
        with pytest.raises(DiscoveryError):
            client(lambda _: httpx.Response(200, json=page())).members(cid)


def test_total_deadline_and_failure_cooldown(tmp_path):
    now, calls = [0.0], []

    def handle(req):
        calls.append(req)
        now[0] += 2
        return httpx.Response(
            200,
            json=page([chat()])
            if req.url.path.endswith("/chats")
            else page([{"member_id": "ou_alice"}]),
        )

    api = client(handle, clock=lambda: now[0])
    discovery = ConversationDiscovery(
        api, ConversationMappings(tmp_path / "ids"), clock=lambda: now[0], scan_seconds=3
    )
    with pytest.raises(DiscoveryError, match="DISCOVERY_BUDGET_EXCEEDED"):
        discovery.list(IDENTITY, ConversationPolicy())
    count = len(calls)
    with pytest.raises(DiscoveryError):
        discovery.list(IDENTITY, ConversationPolicy())
    assert len(calls) == count


def test_uncached_lookup_and_retained_member_budgets(tmp_path):
    now = [0.0]
    api = client(
        lambda req: httpx.Response(
            200,
            json=page([chat("oc_one"), chat("oc_two")])
            if req.url.path.endswith("/chats")
            else page([{"member_id": "ou_alice", "name": "private"}]),
        ),
        clock=lambda: now[0],
    )
    discovery = ConversationDiscovery(
        api,
        ConversationMappings(tmp_path / "ids"),
        clock=lambda: now[0],
        max_lookups=1,
        max_member_ids=1,
    )
    with pytest.raises(DiscoveryError, match="DISCOVERY_BUDGET_EXCEEDED"):
        discovery.list(IDENTITY, ConversationPolicy())
    assert "private" not in repr(discovery.cache)
    assert sum(len(v[1]) for k, v in discovery.cache.items() if "members" in k) <= 1


def test_scan_single_flight_rejects_without_queue(tmp_path):
    started, release = threading.Event(), threading.Event()

    def handle(req):
        started.set()
        assert release.wait(2)
        return httpx.Response(200, json=page())

    discovery = ConversationDiscovery(client(handle), ConversationMappings(tmp_path / "ids"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(discovery.list, IDENTITY, ConversationPolicy())
        assert started.wait(1)
        try:
            second = pool.submit(
                discovery.list, {**IDENTITY, "user_id": "bob"}, ConversationPolicy()
            )
            with pytest.raises(DiscoveryError, match="DISCOVERY_BUSY"):
                second.result(timeout=0.5)
        finally:
            release.set()
        assert first.result(timeout=1)


def test_principal_rate_limit_even_on_cache_hits(tmp_path):
    api = client(lambda _: httpx.Response(200, json=page()), clock=lambda: 0)
    discovery = ConversationDiscovery(api, ConversationMappings(tmp_path / "ids"), clock=lambda: 0)
    for _ in range(6):
        discovery.list(IDENTITY, ConversationPolicy())
    with pytest.raises(DiscoveryError, match="RATE_LIMITED"):
        discovery.list(IDENTITY, ConversationPolicy())
    assert discovery.list({**IDENTITY, "user_id": "bob"}, ConversationPolicy())


def test_total_request_deadline_covers_blocked_transport_and_prevents_worker_queue():
    started, release = threading.Event(), threading.Event()

    def handle(req):
        started.set()
        assert release.wait(2)
        return httpx.Response(200, json=page())

    api = client(handle)
    import time

    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(api.chats, deadline=time.monotonic() + 0.1)
        assert started.wait(1)
        try:
            with pytest.raises(DiscoveryError, match="DISCOVERY_BUDGET_EXCEEDED"):
                request.result(timeout=0.5)
            with pytest.raises(DiscoveryError, match="DISCOVERY_BUSY"):
                api.chats()
        finally:
            release.set()


def test_slow_raw_stream_deadline_and_oversize_are_fail_closed():
    now = [0.0]

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(5):
                now[0] += 1
                yield b" "

    api = client(lambda _: httpx.Response(200, stream=Stream()), clock=lambda: now[0])
    with pytest.raises(DiscoveryError, match="DISCOVERY_BUDGET_EXCEEDED"):
        api.chats(deadline=3)
    api = client(lambda _: httpx.Response(400, content=b"x" * 1_048_577))
    with pytest.raises(DiscoveryError, match="UPSTREAM_PROTOCOL_ERROR"):
        api.chats()


def test_malformed_members_are_not_cached_and_global_failure_cools_other_users(tmp_path):
    calls = []
    api = client(
        lambda req: (
            calls.append(req)
            or httpx.Response(
                200,
                json=page([chat()])
                if req.url.path.endswith("/chats")
                else page([{"member_id": "wrong", "name": "private"}]),
            )
        )
    )
    discovery = ConversationDiscovery(api, ConversationMappings(tmp_path / "ids"))
    with pytest.raises(DiscoveryError, match="UPSTREAM_PROTOCOL_ERROR"):
        discovery.list(IDENTITY, ConversationPolicy())
    assert not any("members" in key for key in discovery.cache)
    count = len(calls)
    with pytest.raises(DiscoveryError, match="RATE_LIMITED"):
        discovery.list({**IDENTITY, "user_id": "bob"}, ConversationPolicy())
    assert len(calls) == count
