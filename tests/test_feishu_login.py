import threading
from urllib.parse import urlsplit, parse_qs

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from engine.auth import AuthService
from engine.backup_runtime import RuntimeSettings, create_runtime_app
from engine.feishu_login import FeishuIdentityProvider, FeishuLoginService, FeishuLoginSettings
from engine.org_store import OrgStore
from engine.voice_audit import VoiceAuditLog


class Provider:
    def exchange(self, code, verifier, redirect):
        assert len(verifier) >= 43
        return {"open_id": "ou_alice", "tenant_key": "team", "name": "Alice"}


@pytest.fixture
def service(tmp_path):
    org = OrgStore(tmp_path / "org")
    audit = VoiceAuditLog(tmp_path / "audit.sqlite")
    auth = AuthService(
        org_store=org, audit_log=audit, edition="enterprise", bootstrap_admin_token="x" * 32
    )
    auth.identity_lock = threading.RLock()
    svc = FeishuLoginService(
        FeishuLoginSettings(
            public_base_url="http://127.0.0.1:8766",
            tenant_orgs={"team": "default-org"},
            backup_target_ids=["vox"],
        ),
        "cli_test",
        auth,
        tmp_path / "login.sqlite",
        Provider(),
    )
    yield svc
    svc.close()
    audit.close()
    org.close()


def ready(svc):
    attempt = svc.begin()
    response = svc.browser(attempt["requestId"])
    assert "code_challenge_method=S256" in response.headers["location"]
    cookie = response.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    response = svc.callback(attempt["requestId"], cookie, "code", "")
    attempt["completion"] = parse_qs(urlsplit(response.headers["location"]).query).get(
        "completion", [None]
    )[0]
    return attempt


def login(svc):
    a = ready(svc)
    return svc.redeem(a["requestId"], a["pollSecret"], a.get("completion"))


def test_registration_repeat_login_same_member_logout_expiry(service):
    one = login(service)
    two = login(service)
    assert one["profile"]["userId"] == two["profile"]["userId"]
    assert service.authenticate(one["token"]).role == "member"
    assert service.me(one["token"])["profile"]["openId"] == "ou_alice"
    service.logout(one["token"])
    assert service.authenticate(one["token"]) is None
    assert service.authenticate(two["token"]) is not None
    service.clock = lambda: two["expiresAt"] + 1
    assert service.authenticate(two["token"]) is None


def test_proof_cookie_replay_and_secret_not_in_poll(service):
    a = service.begin()
    with pytest.raises(HTTPException):
        service.poll(a["requestId"], "wrong")
    with pytest.raises(HTTPException):
        service.callback(a["requestId"], "wrong", "code", "")
    a = ready(service)
    assert "token" not in service.poll(a["requestId"], a["pollSecret"], a.get("completion"))
    service.redeem(a["requestId"], a["pollSecret"], a.get("completion"))
    with pytest.raises(HTTPException):
        service.redeem(a["requestId"], a["pollSecret"], a.get("completion"))
    with pytest.raises(HTTPException):
        service.callback(a["requestId"], "wrong", "code", "")


def test_tenant_and_disabled_user_are_denied(service):
    first = login(service)
    service.auth.org_store.update_user(first["profile"]["userId"], status="disabled")
    assert service.authenticate(first["token"]) is None
    with pytest.raises(HTTPException):
        login(service)
    service.settings.tenant_orgs = {"other": "default-org"}
    a = ready(service)
    assert service.poll(a["requestId"], a["pollSecret"], a.get("completion"))["status"] == "failed"


def test_attempt_expiry_and_rate_limit(service):
    a = service.begin()
    now = service.clock()
    service.clock = lambda: now + 301
    with pytest.raises(HTTPException):
        service.poll(a["requestId"], a["pollSecret"], a.get("completion"))
    for _ in range(5):
        service.begin()
    with pytest.raises(HTTPException) as error:
        service.begin()
    assert error.value.status_code == 429


def test_credential_not_stored_plaintext(service):
    result = login(service)
    row = service.db.execute("SELECT * FROM login_session").fetchone()
    assert result["token"] not in str(tuple(row))


def test_provider_v2_pkce_contract():
    def handle(request):
        if request.url.path == "/open-apis/authen/v2/oauth/token":
            import json

            body = json.loads(request.content)
            assert body["client_secret"] == "protected" and body["code_verifier"] == "verifier"
            return httpx.Response(200, json={"code": 0, "access_token": "sensitive"})
        assert request.headers["Authorization"] == "Bearer sensitive"
        return httpx.Response(
            200, json={"code": 0, "data": {"open_id": "ou_me", "tenant_key": "team", "name": "Me"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        assert (
            FeishuIdentityProvider("cli_test", lambda: "protected", c).exchange(
                "code", "verifier", "http://127.0.0.1/cb"
            )["open_id"]
            == "ou_me"
        )


def test_runtime_routes_and_sso_backup_authorization(tmp_path):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    settings = RuntimeSettings(
        data_dir=str(root),
        app_id="cli_test",
        app_secret_ref="app",
        bootstrap_secret_ref="bootstrap",
        enabled=False,
        targets=[
            {
                "id": "vox",
                "version": 1,
                "org_id": "default-org",
                "chat_id": "oc_test",
                "allowed_users": ["bootstrap-admin"],
            }
        ],
        feishu_login={
            "public_base_url": "http://127.0.0.1:8766",
            "tenant_orgs": {"team": "default-org"},
            "backup_target_ids": ["vox"],
        },
    )
    app = create_runtime_app(settings, read_secret=lambda _: "x" * 32)
    with TestClient(app, base_url="http://127.0.0.1:8766") as c:
        app.state.feishu_login.provider = Provider()
        assert (
            c.post("/v1/auth/feishu/begin", headers={"Host": "rebind.example"}).status_code == 400
        )
        assert (
            c.post(
                "/v1/auth/feishu/begin", headers={"Origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
        a = c.post("/v1/auth/feishu/begin").json()
        r = c.get(
            "/v1/auth/feishu/browser", params={"state": a["requestId"]}, follow_redirects=False
        )
        assert r.status_code == 307 and r.headers["cache-control"] == "no-store"
        r = c.get(
            "/v1/auth/feishu/callback",
            params={"state": a["requestId"], "code": "secretcode"},
            follow_redirects=False,
        )
        assert "secretcode" not in r.text
        completion = parse_qs(urlsplit(r.headers["location"]).query)["completion"][0]
        h = {"Authorization": "Bearer " + a["pollSecret"]}
        assert (
            c.post(
                "/v1/auth/feishu/poll",
                json={"requestId": a["requestId"], "completion": completion},
                headers=h,
            ).json()["status"]
            == "ready"
        )
        token = c.post(
            "/v1/auth/feishu/redeem",
            json={"requestId": a["requestId"], "completion": completion},
            headers=h,
        ).json()["token"]
        h = {"Authorization": "Bearer " + token}
        assert c.get("/v1/auth/feishu/me", headers=h).status_code == 200
        app.state.context.enabled = True
        r = c.post(
            "/v1/input-backups",
            headers=h,
            json={"eventId": "sso-event", "targetId": "vox", "text": "hello"},
        )
        assert r.status_code == 202
        job = app.state.context.backup_store.claim()
        assert app.state.context.authorize_delivery(job)
        assert c.post("/v1/auth/feishu/logout", headers=h).status_code == 200
        assert c.get("/v1/auth/feishu/me", headers=h).status_code == 401


def test_stolen_login_link_and_poll_secret_cannot_redeem(service):
    a = ready(service)
    with pytest.raises(HTTPException):
        service.poll(a["requestId"], a["pollSecret"])
    with pytest.raises(HTTPException):
        service.redeem(a["requestId"], a["pollSecret"])
    with pytest.raises(HTTPException):
        service.redeem(a["requestId"], a["pollSecret"], "wrong-device")
    result = service.redeem(a["requestId"], a["pollSecret"], a["completion"])
    assert result["profile"]["name"] == "Alice"


def test_sessions_survive_restart_but_login_attempts_do_not(service, tmp_path):
    result = login(service)
    waiting = ready(service)
    restarted = FeishuLoginService(
        service.settings, service.app_id, service.auth, tmp_path / "login.sqlite", Provider()
    )
    try:
        assert restarted.authenticate(result["token"]).user_id == result["profile"]["userId"]
        with pytest.raises(HTTPException):
            restarted.redeem(waiting["requestId"], waiting["pollSecret"], waiting["completion"])
    finally:
        restarted.close()


def test_concurrent_redemption_issues_only_one_session(service):
    from concurrent.futures import ThreadPoolExecutor

    a = ready(service)

    def redeem_once():
        try:
            return service.redeem(a["requestId"], a["pollSecret"], a["completion"])["token"]
        except HTTPException:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: redeem_once(), range(2)))
    assert sum(value is not None for value in results) == 1
    assert service.db.execute("SELECT count(*) FROM login_session").fetchone()[0] == 1


def test_rate_limits_do_not_share_one_client_bucket(service):
    for _ in range(5):
        service.begin("client-one")
    with pytest.raises(HTTPException):
        service.begin("client-one")
    assert service.begin("client-two")["requestId"]


def test_conversation_guard_rejects_stale_principal_organization(service):
    from dataclasses import replace

    session = login(service)
    principal = service.authenticate(session["token"])
    assert service.resolve_conversation_principal(principal)
    assert not service.resolve_conversation_principal(replace(principal, org_id="other-org"))
    service.auth.org_store.update_user(principal.user_id, status="disabled")
    assert not service.resolve_conversation_principal(principal)
