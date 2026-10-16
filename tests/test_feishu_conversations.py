"""Offline discovery contract tests; all upstream traffic uses MockTransport."""

import httpx
import pytest

from engine.feishu_client import FeishuClient, DiscoveryError
from engine.feishu_conversations import ConversationDiscovery, ConversationMappings
from engine.backup_app import ConversationPolicy


def page(items=(), **kw):
    return {"code": 0, "data": {"items": list(items), "has_more": False, **kw}}


def chat(id="oc_one", **kw):
    return {"chat_id": id, "name": id, "external": False, "tenant_key": "team", **kw}


def client(handler, **kw):
    def transport(req):
        if req.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "synthetic", "expire": 7200}
            )
        return handler(req)

    return FeishuClient(
        "cli_test", "team", lambda: "synthetic", transport=httpx.MockTransport(transport), **kw
    )


def test_intersection_pagination_filters_order_and_persistent_isolation(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/chats"):
            return httpx.Response(
                200,
                json=page(
                    [
                        chat(),
                        chat("oc_pin"),
                        chat("oc_absent"),
                        chat("oc_external", external=True),
                        chat("oc_unknown", external=None),
                        chat("oc_wrong", tenant_key="other"),
                        chat("oc_missing", tenant_key=None),
                        chat("oc_denied"),
                    ]
                ),
            )
        if not req.url.params.get("page_token"):
            return httpx.Response(200, json=page([], has_more=True, page_token="next"))
        return httpx.Response(
            200,
            json=page([{"member_id": "ou_alice" if "absent" not in req.url.path else "ou_other"}]),
        )

    api = client(handler)
    path = tmp_path / "mapping.sqlite"
    mapping = ConversationMappings(path)
    discovery = ConversationDiscovery(api, mapping)
    policy = ConversationPolicy(pinned_chat_ids=["oc_pin"], denied_chat_ids=["oc_denied"])
    identity = {"app_id": "cli_test", "tenant": "team", "user_id": "alice", "open_id": "ou_alice"}
    result = discovery.list(identity, policy)
    assert [x["name"] for x in result] == ["Feishu CLI", "oc_pin", "oc_one"]
    assert all(x["id"].startswith("fc_") and x["id"] != "fc_dm" for x in result)
    assert "chat_id" not in str(result)
    assert discovery.list(identity, policy) == result
    assert len(calls) == 7
    assert discovery.list({**identity, "user_id": "bob"}, policy)[0]["id"] != result[0]["id"]
    mapping.close()
    mapping = ConversationMappings(path)
    assert ConversationDiscovery(api, mapping).list(identity, policy) == result
    mapping.close()
    api.close()


@pytest.mark.parametrize(
    "data",
    [
        page([], has_more=True),
        page([], has_more=True, page_token="same"),
        page([], has_more="false"),
        {"code": True},
        page(["bad"]),
        {"code": 99991672, "msg": "secret"},
        {"code": 230020, "msg": "secret"},
    ],
)
def test_pagination_and_business_errors_fail_closed(data):
    api = client(lambda _: httpx.Response(200, json=data))
    with pytest.raises(DiscoveryError) as error:
        api.chats()
    assert "secret" not in str(error.value)
    api.close()


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_http_errors_static(status):
    api = client(lambda _: httpx.Response(status, text="private-token-body"))
    with pytest.raises(DiscoveryError) as error:
        api.chats()
    assert "private" not in str(error.value)
    api.close()


def test_missing_membership_permission_never_returns_dm_only(tmp_path):
    api = client(
        lambda req: (
            httpx.Response(200, json=page([chat()]))
            if req.url.path.endswith("/chats")
            else httpx.Response(403)
        )
    )
    mapping = ConversationMappings(tmp_path / "ids.sqlite")
    with pytest.raises(DiscoveryError):
        ConversationDiscovery(api, mapping).list(
            {"app_id": "cli_test", "tenant": "team", "user_id": "alice", "open_id": "ou_alice"},
            ConversationPolicy(),
        )
    mapping.close()
    api.close()


def test_rate_budget_and_cooldown_are_bounded():
    now = [0.0]
    calls = []
    api = client(lambda req: calls.append(req) or httpx.Response(429), clock=lambda: now[0])
    for _ in range(2):
        with pytest.raises(DiscoveryError, match="RATE_LIMITED"):
            api.chats()
    assert len(calls) == 1
    now[0] = 61
    with pytest.raises(DiscoveryError):
        api.chats()
    assert len(calls) == 2
    api.close()


def test_runtime_route_uses_persisted_identity_and_no_startup_calls(tmp_path):
    from fastapi.testclient import TestClient
    from engine.backup_runtime import create_runtime_app
    from tests.test_backup_runtime import settings, secret
    from tests.test_feishu_login import Provider, login

    calls = []

    def handle(req):
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
        conversation_transport_factory=lambda: httpx.MockTransport(handle),
    )
    with TestClient(app, base_url="http://127.0.0.1:8766") as http:
        assert calls == []
        svc = app.state.feishu_login
        svc.provider = Provider()
        result = login(svc)
        headers = {"Authorization": "Bearer " + result["token"]}
        response = http.get("/v1/conversations?user_id=mallory&tenant=other", headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["identity"] == {
            "appId": "cli_test",
            "tenantKey": "team",
            "userId": result["profile"]["userId"],
        }
        first_id = response.json()["conversations"][0]["id"]
        assert (
            http.get("/v1/conversations", headers=headers).json()["conversations"][0]["id"]
            == first_id
        )
        assert (
            http.get(
                "/v1/conversations", headers={"Authorization": "Bearer " + secret("bootstrap")}
            ).status_code
            == 403
        )
        with svc.db:
            svc.db.execute("UPDATE feishu_identity SET app_id='cli_wrong'")
        assert http.get("/v1/conversations", headers=headers).status_code in (401, 403)
        with svc.db:
            svc.db.execute("UPDATE feishu_identity SET app_id='cli_test', tenant='other'")
        assert http.get("/v1/conversations", headers=headers).status_code in (401, 403)
        with svc.db:
            svc.db.execute("UPDATE feishu_identity SET tenant='team'")
        svc.auth.org_store.update_user(result["profile"]["userId"], status="disabled")
        assert http.get("/v1/conversations", headers=headers).status_code in (401, 403)
        svc.auth.org_store.update_user(result["profile"]["userId"], status="active")
        # Invalidated sessions stay revoked after an account is re-enabled.
        assert http.get("/v1/conversations", headers=headers).status_code == 401
        fresh = login(svc)
        headers = {"Authorization": "Bearer " + fresh["token"]}
        app.state.context.conversations_enabled = False
        disabled = http.get("/v1/conversations", headers=headers)
        assert disabled.status_code == 503
        assert disabled.headers["cache-control"] == "no-store"


def test_cache_expiry_does_not_fallback_to_stale_membership(tmp_path):
    now = [0]
    fail = [False]

    def handler(req):
        if req.url.path.endswith("/chats"):
            return httpx.Response(200, json=page([chat()]))
        return (
            httpx.Response(500)
            if fail[0]
            else httpx.Response(200, json=page([{"member_id": "ou_alice"}]))
        )

    api = client(handler, clock=lambda: now[0])
    mapping = ConversationMappings(tmp_path / "map.sqlite")
    discovery = ConversationDiscovery(api, mapping, clock=lambda: now[0])
    identity = {"app_id": "cli_test", "tenant": "team", "user_id": "alice", "open_id": "ou_alice"}
    assert len(discovery.list(identity, ConversationPolicy())) == 2
    fail[0] = True
    now[0] = 301
    with pytest.raises(DiscoveryError):
        discovery.list(identity, ConversationPolicy())
    mapping.close()
    api.close()


def test_page_budget_network_errors_and_local_rate_ceiling():
    api = client(
        lambda _: httpx.Response(200, json=page([], has_more=True, page_token="next")), max_pages=1
    )
    with pytest.raises(DiscoveryError, match="PAGINATION_EXHAUSTED"):
        api.chats()
    api.close()

    def fail(_):
        raise RuntimeError("private body and token")

    api = client(fail)
    with pytest.raises(DiscoveryError) as error:
        api.chats()
    assert "private" not in str(error.value)
    assert error.value.__context__ is None
    api.close()
    api = client(lambda _: httpx.Response(200, json=page()), clock=lambda: 0)
    for _ in range(19):
        api.chats()
    with pytest.raises(DiscoveryError, match="RATE_LIMITED"):
        api.chats()
    api.close()


def test_mapping_scope_includes_app_tenant_and_user(tmp_path):
    mapping = ConversationMappings(tmp_path / "scoped.sqlite")
    base = {"app_id": "cli_test", "tenant": "team", "user_id": "alice"}
    identities = [
        base,
        {**base, "app_id": "cli_other"},
        {**base, "tenant": "other"},
        {**base, "user_id": "bob"},
    ]
    ids = [mapping.identifiers(identity, ["dm", "oc_shared"]) for identity in identities]
    assert len({id for pair in ids for id in pair}) == 8
    assert mapping.identifiers(base, ["dm", "oc_shared"]) == ids[0]
    mapping.close()


def test_injected_exception_cannot_smuggle_body_into_static_error():
    api = client(lambda _: (_ for _ in ()).throw(DiscoveryError("private-token-body")))
    with pytest.raises(DiscoveryError, match="^UPSTREAM_UNAVAILABLE$"):
        api.chats()
    api.close()


def test_chat_pages_follow_explicit_cursor_even_when_empty():
    requests = []

    def handler(req):
        requests.append(req)
        assert req.url.scheme == "https" and req.url.host == "open.feishu.cn"
        assert req.headers["authorization"] == "Bearer synthetic"
        if "page_token" not in req.url.params:
            return httpx.Response(200, json=page([], has_more=True, page_token="second"))
        assert req.url.params["page_token"] == "second"
        return httpx.Response(200, json=page([chat()]))

    api = client(handler)
    assert api.chats() == [chat()]
    assert len(requests) == 2
    api.close()


@pytest.mark.parametrize(
    "token_response",
    [
        {"code": 0, "tenant_access_token": "private", "expire": True},
        {"code": 0, "tenant_access_token": "private\nheader", "expire": 7200},
        {"code": 0, "expire": 7200},
        {"code": 1, "msg": "private-token-body"},
    ],
)
def test_token_response_validation_never_discloses_secret(token_response):
    api = FeishuClient(
        "cli_test",
        "team",
        lambda: "synthetic",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=token_response)),
    )
    with pytest.raises(DiscoveryError) as error:
        api.chats()
    assert "private" not in str(error.value)
    api.close()
