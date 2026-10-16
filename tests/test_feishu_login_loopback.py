"""T24a: the desktop loopback return for Feishu sign-in (owner decision D5).

The Windows app listens on 127.0.0.1:<port> and passes that port to /begin;
the callback then returns the browser there instead of the macOS URI scheme.
The completion is still useless without the pollSecret held by the app.
"""
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from engine.backup_runtime import create_runtime_app
from tests.test_backup_runtime import secret, settings
from tests.test_feishu_login import Provider, service  # noqa: F401 (fixture)


def callback_location(svc, attempt):
    response = svc.browser(attempt["requestId"])
    cookie = response.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    return svc.callback(attempt["requestId"], cookie, "code", "").headers["location"]


def test_a_loopback_port_returns_the_browser_to_127_0_0_1(service):  # noqa: F811
    attempt = service.begin(loopback_port=53123)
    location = callback_location(service, attempt)
    parts = urlsplit(location)
    assert (parts.scheme, parts.hostname, parts.port, parts.path) == (
        "http", "127.0.0.1", 53123, "/vox-agent/login-complete")
    query = parse_qs(parts.query)
    assert query["requestId"] == [attempt["requestId"]]
    redeemed = service.redeem(attempt["requestId"], attempt["pollSecret"], query["completion"][0])
    assert redeemed["token"].startswith("vox_sso_")


def test_without_a_port_the_macos_scheme_is_unchanged(service):  # noqa: F811
    location = callback_location(service, service.begin())
    assert location.startswith("com.osborn.voxagent.auth://login-complete?")


def test_a_failed_login_also_returns_to_the_loopback_without_a_completion(service):  # noqa: F811
    class Denied:
        def exchange(self, code, verifier, redirect):
            raise ValueError("denied")

    service.provider = Denied()
    attempt = service.begin(loopback_port=53124)
    query = parse_qs(urlsplit(callback_location(service, attempt)).query)
    assert query["error"] == ["login_failed"] and "completion" not in query


def test_the_completion_from_a_captured_redirect_cannot_redeem_without_the_poll_secret(service):  # noqa: F811
    attempt = service.begin(loopback_port=53125)
    completion = parse_qs(urlsplit(callback_location(service, attempt)).query)["completion"][0]
    with pytest.raises(HTTPException) as denied:
        service.redeem(attempt["requestId"], "wrong-secret-value-of-sufficient-length", completion)
    assert denied.value.status_code == 401


@pytest.mark.parametrize("port", [0, 80, 1023, 65536, -1])
def test_out_of_range_ports_are_refused(service, port):  # noqa: F811
    with pytest.raises(ValueError):
        service.begin(loopback_port=port)


def test_the_begin_route_accepts_an_optional_strict_body(tmp_path):
    app = create_runtime_app(
        settings(tmp_path, feishu_login={"public_base_url": "http://127.0.0.1:8766",
                                         "tenant_orgs": {"team": "default-org"}}),
        read_secret=secret,
    )
    with TestClient(app, base_url="http://127.0.0.1:8766") as http:
        assert http.post("/v1/auth/feishu/begin").status_code == 200
        assert http.post("/v1/auth/feishu/begin", json={"loopbackPort": 53126}).status_code == 200
        for body in [{"loopbackPort": True}, {"loopbackPort": "53126"}, {"loopbackPort": 80},
                     {"loopbackPort": 53126, "returnTo": "https://evil.example"}]:
            assert http.post("/v1/auth/feishu/begin", json=body).status_code == 422, body
