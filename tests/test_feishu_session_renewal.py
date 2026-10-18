"""Offline renewable-session security contracts; never uses tenant credentials."""

import threading

import httpx
import pytest
from fastapi import HTTPException

from engine.feishu_client import FeishuClient, DiscoveryError
from engine.feishu_login import FeishuLoginService
from tests.test_feishu_login import service as service_fixture, login

service = service_fixture


def setup_clock(svc):
    now = [1_800_000_000.0]
    svc.clock = lambda: now[0]
    svc.provider.member_active = lambda tenant, user: tenant == "team" and user == "ou_alice"
    return now


def test_renew_after_access_expiry_hash_only_and_restart(service):
    now = setup_clock(service)
    first = login(service)
    now[0] += 3601
    assert service.authenticate(first["token"]) is None
    newer = service.renew(first["refreshToken"])
    assert newer["profile"] == first["profile"]
    assert newer["token"] != first["token"]
    assert newer["refreshToken"] == first["refreshToken"]
    assert service.authenticate(newer["token"])
    dump = " ".join(service.db.iterdump())
    assert first["refreshToken"] not in dump and newer["token"] not in dump
    path = service.db.execute("PRAGMA database_list").fetchone()[2]
    restarted = FeishuLoginService(
        service.settings,
        service.app_id,
        service.auth,
        __import__("pathlib").Path(path),
        service.provider,
        clock=service.clock,
    )
    try:
        assert restarted.renew(first["refreshToken"])["profile"] == first["profile"]
    finally:
        restarted.close()


def test_response_loss_retry_and_stale_access_logout(service):
    setup_clock(service)
    first = login(service)
    lost = service.renew(first["refreshToken"])
    received = service.renew(first["refreshToken"])
    assert service.authenticate(lost["token"]) and service.authenticate(received["token"])
    service.logout(first["token"])
    assert service.authenticate(received["token"]) is None
    with pytest.raises(HTTPException) as e:
        service.renew(first["refreshToken"])
    assert e.value.status_code == 401


def test_idle_expiry_and_refresh_not_access(service):
    now = setup_clock(service)
    first = login(service)
    assert service.authenticate(first["refreshToken"]) is None
    now[0] += 90 * 86400 + 1
    with pytest.raises(HTTPException) as e:
        service.renew(first["refreshToken"])
    assert e.value.status_code == 401


@pytest.mark.parametrize("mode", ["local", "remote"])
def test_disabled_does_not_resurrect(service, mode):
    setup_clock(service)
    first = login(service)
    user = first["profile"]["userId"]
    if mode == "local":
        service.auth.org_store.update_user(user, status="disabled")
    else:
        service.provider.member_active = lambda *_: False
    with pytest.raises(HTTPException) as e:
        service.renew(first["refreshToken"])
    assert e.value.status_code == 401
    service.auth.org_store.update_user(user, status="active")
    service.provider.member_active = lambda *_: True
    assert service.authenticate(first["token"]) is None
    with pytest.raises(HTTPException):
        service.renew(first["refreshToken"])


def test_outage_preserves_refresh_and_no_extension(service):
    now = setup_clock(service)
    first = login(service)
    before = service.db.execute("SELECT expires FROM login_device").fetchone()[0]

    def down(*_):
        raise RuntimeError("private upstream body")

    service.provider.member_active = down
    now[0] += 3601
    with pytest.raises(HTTPException) as e:
        service.renew(first["refreshToken"])
    assert e.value.status_code == 503 and "private" not in str(e.value)
    assert service.db.execute("SELECT expires FROM login_device").fetchone()[0] == before
    service.provider.member_active = lambda *_: True
    assert service.renew(first["refreshToken"])["token"]


def test_logout_wins_against_remote_check(service):
    setup_clock(service)
    first = login(service)
    entered, release = threading.Event(), threading.Event()

    def remote(*_):
        entered.set()
        assert release.wait(2)
        return True

    service.provider.member_active = remote
    errors = []

    def renew():
        try:
            service.renew(first["refreshToken"])
        except HTTPException as e:
            errors.append(e.status_code)

    thread = threading.Thread(target=renew)
    thread.start()
    assert entered.wait(2)
    # This must not wait behind a network-held identity lock.
    service.logout(first["refreshToken"])
    release.set()
    thread.join(2)
    assert not thread.is_alive() and errors == [401]


def test_recheck_org_after_network(service):
    setup_clock(service)
    first = login(service)

    def remote(*_):
        service.settings.tenant_orgs = {"another": "default-org"}
        return True

    service.provider.member_active = remote
    with pytest.raises(HTTPException) as e:
        service.renew(first["refreshToken"])
    assert e.value.status_code == 401


@pytest.mark.parametrize(
    "flag", [None, "is_frozen", "is_resigned", "is_exited", "is_unjoin", "missing", "wrong_id"]
)
def test_directory_contract_fails_closed(flag):
    status = dict(
        is_activated=True, is_frozen=False, is_resigned=False, is_exited=False, is_unjoin=False
    )
    if flag in status:
        status[flag] = True
    if flag == "missing":
        del status["is_exited"]

    def wire(request):
        if request.url.path.endswith("/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "synthetic", "expire": 7200}
            )
        assert str(request.url).endswith("/contact/v3/users/ou_alice?user_id_type=open_id")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "user": {
                        "open_id": "ou_other" if flag == "wrong_id" else "ou_alice",
                        "status": status,
                    }
                },
            },
        )

    client = FeishuClient(
        "cli_test", "team", lambda: "synthetic", transport=httpx.MockTransport(wire)
    )
    try:
        if flag in ("missing", "wrong_id"):
            with pytest.raises(DiscoveryError):
                client.member_active("ou_alice")
        else:
            assert client.member_active("ou_alice") == (flag is None)
    finally:
        client.close()


def test_admin_revocation_and_previous_ownership_routes(service, tmp_path):
    from fastapi.testclient import TestClient
    from engine.backup_app import BackupAppContext, create_backup_app
    from engine.input_backup import BackupStore

    setup_clock(service)
    first = login(service)
    store = BackupStore(tmp_path / "backups.sqlite")
    auth = service.auth.authenticate
    service.auth.authenticate = lambda token: (
        service.authenticate(token) if token.startswith("vox_sso_") else auth(token)
    )
    app = create_backup_app(BackupAppContext(service.auth, store, ()), enable_feishu_login=True)
    app.state.feishu_login = service
    try:
        with TestClient(app, base_url="http://127.0.0.1:8766") as client:
            bearer = {"Authorization": "Bearer " + first["token"]}
            path = "/v1/auth/feishu/users/" + first["profile"]["userId"] + "/revoke"
            assert client.post(path, headers=bearer).status_code == 403
            renewed = client.post(
                "/v1/auth/feishu/renew",
                headers={"Authorization": "Bearer " + first["refreshToken"]},
            )
            assert renewed.status_code == 200 and renewed.json()["expiresIn"] == 3600
            assert (
                client.get(
                    "/v1/auth/feishu/me",
                    headers={"Authorization": "Bearer " + first["refreshToken"]},
                ).status_code
                == 401
            )
            same = client.post(
                "/v1/auth/feishu/link-previous",
                headers=bearer,
                json={"previousToken": first["token"]},
            )
            assert same.status_code == 200 and same.json()["sameIdentity"] is True
            different = client.post(
                "/v1/auth/feishu/link-previous",
                headers=bearer,
                json={"previousToken": "vox_sso_" + "a" * 43},
            )
            assert different.json()["sameIdentity"] is False
            assert (
                client.post(path, headers={"Authorization": "Bearer " + "x" * 32}).status_code
                == 200
            )
            assert service.authenticate(first["token"]) is None
            assert (
                client.post(
                    "/v1/auth/feishu/renew",
                    headers={"Authorization": "Bearer " + first["refreshToken"]},
                ).status_code
                == 401
            )
    finally:
        store.close()


def test_per_user_rate_limit_across_devices(service):
    setup_clock(service)
    first = login(service)
    other = login(service)
    for _ in range(6):
        service.renew(first["refreshToken"])
    with pytest.raises(HTTPException) as e:
        service.renew(other["refreshToken"])
    assert e.value.status_code == 429


def test_legacy_access_capped_on_upgrade(service):
    from pathlib import Path
    from engine.feishu_login import digest

    now = setup_clock(service)
    first = login(service)
    with service.db:
        service.db.execute("DELETE FROM login_device_access")
        service.db.execute("UPDATE login_session SET expires=?", (now[0] + 86400,))
    path = Path(service.db.execute("PRAGMA database_list").fetchone()[2])
    restarted = FeishuLoginService(
        service.settings, service.app_id, service.auth, path, service.provider, clock=service.clock
    )
    try:
        row = restarted.db.execute(
            "SELECT expires FROM login_session WHERE hash=?", (digest(first["token"]),)
        ).fetchone()
        assert row[0] == now[0] + 3600
    finally:
        restarted.close()


def test_device_limit_evicts_oldest_without_lockout(service):
    now = setup_clock(service)
    first = login(service)
    latest = first
    for _ in range(20):
        now[0] += 61  # Independent successful interactive sign-ins.
        latest = login(service)
    assert service.authenticate(first["token"]) is None
    assert service.authenticate(latest["token"]) is not None
    count = service.db.execute("SELECT COUNT(*) FROM login_device WHERE revoked=0").fetchone()[0]
    assert count == 20


def test_pruning_preserves_current_family_and_removes_old_proof(service):
    from engine.feishu_login import digest

    now = setup_clock(service)
    first = login(service)
    now[0] += 80 * 86400
    recent = service.renew(first["refreshToken"])
    now[0] += 14 * 86400
    current = service.renew(first["refreshToken"])
    assert service.authenticate(current["token"])
    assert (
        service.db.execute(
            "SELECT hash FROM login_session WHERE hash=?", (digest(first["token"]),)
        ).fetchone()
        is None
    )
    assert (
        service.db.execute(
            "SELECT hash FROM login_session_identity WHERE hash=?", (digest(first["token"]),)
        ).fetchone()
        is None
    )
    assert service.db.execute(
        "SELECT hash FROM login_session_identity WHERE hash=?", (digest(recent["token"]),)
    ).fetchone()


def test_each_renewal_outcome_logs_one_structured_line_without_identifiers(service, caplog):
    import logging

    from engine.feishu_login import digest

    setup_clock(service)
    first = login(service)
    refresh = first["refreshToken"]
    user_id = first["profile"]["userId"]
    device_id = service.db.execute(
        "SELECT id FROM login_device WHERE user_id=?", (user_id,)
    ).fetchone()[0]
    logger = "engine.feishu_session_renewal"

    def lines():
        return [
            r for r in caplog.records
            if r.name == logger and r.getMessage().startswith("Feishu session renewal")
        ]

    def only_line():
        records = lines()
        assert len(records) == 1
        message = records[0].getMessage()
        for secret in (refresh, user_id, device_id, "ou_alice", "team", "private"):
            assert secret not in message
        return records[0], message

    def attempt():
        caplog.clear()
        try:
            service.renew(refresh)
        except HTTPException as error:
            return error.detail
        return "RENEWED"

    with caplog.at_level(logging.INFO, logger=logger):
        assert attempt() == "RENEWED"
        record, message = only_line()
        assert record.levelno == logging.INFO
        assert f"user={digest(user_id)[:12]}" in message
        assert f"device={device_id[:8]}" in message
        assert "outcome=RENEWED" in message and "upstream=none" in message

        def denied(*_):
            raise DiscoveryError("PERMISSION_DENIED")

        service.provider.member_active = denied
        assert attempt() == "MEMBERSHIP_CHECK_UNAVAILABLE"
        record, message = only_line()
        assert record.levelno == logging.WARNING
        assert "outcome=MEMBERSHIP_CHECK_UNAVAILABLE" in message
        assert "upstream=PERMISSION_DENIED" in message

        def crashed(*_):
            raise RuntimeError('{"code": 99991663, "msg": "private upstream body ou_alice"}')

        service.provider.member_active = crashed
        assert attempt() == "MEMBERSHIP_CHECK_UNAVAILABLE"
        _, message = only_line()
        assert "upstream=RuntimeError" in message

        service.provider.member_active = lambda *_: True
        service.settings.tenant_orgs = {"team": "default-org", "another": "default-org"}
        assert attempt() == "MEMBERSHIP_CHECK_UNAVAILABLE"
        _, message = only_line()
        assert "upstream=TENANT_CONFIG" in message
        service.settings.tenant_orgs = {"team": "default-org"}

        for _ in range(3):
            assert attempt() == "RENEWED"
        assert attempt() == "RENEWAL_RATE_LIMIT"
        _, message = only_line()
        assert "outcome=RENEWAL_RATE_LIMIT" in message and "upstream=none" in message

        service.renew_starts = []
        service.provider.member_active = lambda *_: False
        assert attempt() == "ACCOUNT_DISABLED"
        _, message = only_line()
        assert "outcome=ACCOUNT_DISABLED" in message and "upstream=none" in message
        assert f"user={digest(user_id)[:12]}" in message

        assert attempt() == "SESSION_EXPIRED"
        _, message = only_line()
        assert "outcome=SESSION_EXPIRED" in message

        caplog.clear()
        with pytest.raises(HTTPException) as e:
            service.renew("vox_refresh_" + "x" * 43)
        assert e.value.detail == "SESSION_EXPIRED"
        _, message = only_line()
        assert "user=- device=-" in message


def test_concurrent_renewal_logs_in_progress(service, caplog):
    import logging

    setup_clock(service)
    first = login(service)
    entered, release = threading.Event(), threading.Event()

    def remote(*_):
        entered.set()
        assert release.wait(2)
        return True

    service.provider.member_active = remote
    thread = threading.Thread(target=lambda: service.renew(first["refreshToken"]))
    with caplog.at_level(logging.INFO, logger="engine.feishu_session_renewal"):
        thread.start()
        assert entered.wait(2)
        with pytest.raises(HTTPException) as e:
            service.renew(first["refreshToken"])
        assert e.value.status_code == 409
        release.set()
        thread.join(2)
    outcomes = [
        r.getMessage().split("outcome=")[1].split()[0]
        for r in caplog.records
        if r.getMessage().startswith("Feishu session renewal")
    ]
    assert sorted(outcomes) == ["RENEWAL_IN_PROGRESS", "RENEWED"]
