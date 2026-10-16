"""Phase 2 guard behavior: synthetic identities and fake OAuth provider only."""

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from engine.auth import Principal, require_principal
from engine.backup_app import require_feishu_principal
from engine.backup_runtime import create_runtime_app
from tests.test_backup_runtime import secret, settings
from tests.test_feishu_login import Provider, login


@pytest.fixture
def guarded(tmp_path):
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
    )

    # Test-only route: Phase 2 does not expose conversation APIs.
    @app.get("/guard-test")
    def guarded_route(principal=Depends(require_feishu_principal)):
        return {"user": principal.user_id, "role": principal.role}

    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        svc = app.state.feishu_login
        svc.provider = Provider()
        session = login(svc)
        yield app, client, svc, session


def request(client, token, **headers):
    return client.get("/guard-test", headers={"Authorization": "Bearer " + token, **headers})


def test_valid_session_and_independent_switch(guarded):
    app, client, _, session = guarded
    assert request(client, session["token"]).status_code == 200
    assert not app.state.context.enabled
    app.state.context.conversations_enabled = False
    assert request(client, session["token"]).status_code == 503


@pytest.mark.parametrize("kind", ["bootstrap", "bearer", "prefix", "missing", "revoked", "expired"])
def test_rejects_non_session_credentials(guarded, kind):
    app, client, svc, session = guarded
    token = session["token"]
    if kind == "bootstrap":
        token = secret("bootstrap")
    elif kind == "bearer":
        _, token = app.state.context.auth_service.issue_credential(
            session["profile"]["userId"], "test"
        )
    elif kind == "prefix":
        token = "vox_sso_not-a-session"
    elif kind == "missing":
        assert client.get("/guard-test").status_code == 401
        return
    elif kind == "revoked":
        svc.logout(token)
    else:
        svc.clock = lambda: session["expiresAt"] + 1
    assert request(client, token).status_code in (401, 403)


@pytest.mark.parametrize(
    "mutation", ["app", "tenant", "org", "disabled", "missing_identity", "missing_user"]
)
@pytest.mark.parametrize("stale_principal", [False, True])
def test_rechecks_current_identity_even_after_authentication(guarded, mutation, stale_principal):
    app, client, svc, session = guarded
    uid = session["profile"]["userId"]
    if stale_principal:
        principal = svc.authenticate(session["token"])
        app.dependency_overrides[require_principal] = lambda: principal
    if mutation in ("app", "tenant"):
        column = "app_id" if mutation == "app" else "tenant"
        svc.db.execute(f"UPDATE feishu_identity SET {column}=?", ("wrong",))
    elif mutation == "org":
        svc.settings.tenant_orgs = {"team": "other-org"}
    elif mutation == "disabled":
        svc.auth.org_store.update_user(uid, status="disabled")
    elif mutation == "missing_identity":
        svc.db.execute("DELETE FROM feishu_identity")
    else:
        svc.auth.org_store.get_user = lambda _: None
    assert request(client, session["token"]).status_code in (401, 403)


@pytest.mark.parametrize("method", ["bootstrap", "bearer", "local"])
def test_auth_method_is_required_even_for_mapped_user(guarded, method):
    app, client, _, session = guarded
    principal = Principal(session["profile"]["userId"], "default-org", "admin", method)
    app.dependency_overrides[require_principal] = lambda: principal
    assert request(client, session["token"]).status_code == 403


def test_headers_cannot_supply_identity_or_replace_host(guarded):
    _, client, _, _ = guarded
    assert (
        request(
            client,
            secret("bootstrap"),
            **{
                "X-Auth-Method": "feishu",
                "X-Tenant": "team",
                "X-User-Id": "ou_alice",
                "Forwarded": "for=127.0.0.1;host=127.0.0.1:8766;proto=https",
            },
        ).status_code
        == 403
    )
    assert (
        request(
            client,
            secret("bootstrap"),
            **{
                "Host": "evil.invalid",
                "X-Forwarded-Host": "127.0.0.1:8766",
            },
        ).status_code
        == 400
    )


def test_missing_login_and_multi_tenant_fail_closed(guarded):
    app, client, svc, session = guarded
    svc.settings.tenant_orgs["second"] = "default-org"
    assert request(client, session["token"]).status_code == 403
    app.state.context.auth_service.feishu_login = None
    assert request(client, session["token"]).status_code in (401, 403)


def test_guard_returns_current_role_and_rejects_missing_organization(guarded):
    app, client, svc, session = guarded
    principal = svc.authenticate(session["token"])
    app.dependency_overrides[require_principal] = lambda: principal
    svc.auth.org_store.update_user(principal.user_id, role="admin")
    assert request(client, session["token"]).json()["role"] == "admin"
    svc.auth.org_store.get_organization = lambda _: None
    assert request(client, session["token"]).status_code == 403


def test_ineligible_principal_denial_audit_excludes_request_content(guarded):
    app, client, svc, session = guarded
    marker = "private-request-marker"
    assert request(client, secret("bootstrap"), **{"X-Private": marker}).status_code == 403
    events = app.state.context.auth_service.audit_log.list_events()
    denied = [e for e in events if e["action"] == "feishu.conversation.authorization.denied"]
    assert len(denied) == 1
    assert denied[0]["actorId"] == "bootstrap-admin"
    assert denied[0]["metadata"] == {"reason": "FEISHU_PRINCIPAL_REQUIRED"}
    assert marker not in str(denied) and secret("bootstrap") not in str(denied)
