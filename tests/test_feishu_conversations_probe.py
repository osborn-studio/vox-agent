"""Offline capability evidence; never credentials or a real tenant."""

import json

import httpx
import pytest

from scripts.verify_feishu_conversations import ProbeError, dry_run, probe, main


def page(items=(), more=False, token=None):
    return {"code": 0, "data": {"items": list(items), "has_more": more, "page_token": token}}


def run(responses, operation="chats", **kwargs):
    calls = []

    def handle(request):
        calls.append(request)
        response = responses.pop(0)
        return (
            httpx.Response(*response)
            if isinstance(response, tuple)
            else httpx.Response(200, json=response)
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = probe(client, operation, chat_id="oc_fixture", **kwargs)
    return result, calls


def test_short_and_empty_pages_follow_has_more_not_item_count():
    result, calls = run(
        [
            page([{"external": True, "tenant_key": "fixture"}], True, "next"),
            page([], True, "last"),
            page([{"external": False, "tenant_key": "fixture"}]),
        ],
        max_pages=3,
    )
    assert result == {
        "operation": "chats",
        "pages": 3,
        "items": 2,
        "complete": True,
        "external_chats": 1,
        "missing_boundary_fields": 0,
    }
    assert [r.url.params.get("page_token") for r in calls] == [None, "next", "last"]


def test_unknown_external_or_tenant_is_not_treated_as_internal():
    result, _ = run([page([{}, {"external": "false", "tenant_key": "fixture"}])])
    assert result["missing_boundary_fields"] == 2


def test_members_use_open_id_and_paginate():
    result, calls = run(
        [page([], True, "next"), page([{"member_id": "ou_fixture"}])],
        operation="members",
        max_pages=2,
    )
    assert result["items"] == 1
    assert all(r.url.params["member_id_type"] == "open_id" for r in calls)


@pytest.mark.parametrize(
    "data",
    [
        page([], True),
        page([], True, ""),
        {"code": False, "data": {}},
        {"code": 0, "data": {"has_more": "false", "items": []}},
    ],
)
def test_malformed_response_fails_closed(data):
    with pytest.raises(ProbeError, match="malformed_response"):
        run([data])


def test_cyclic_cursor_and_page_budget_fail_without_partial_success():
    with pytest.raises(ProbeError, match="pagination_cycle"):
        run([page([], True, "next"), page([], True, "next")], max_pages=3)
    with pytest.raises(ProbeError, match="page_budget_exhausted"):
        run([page([], True, "next")])


@pytest.mark.parametrize(
    "status,code,kind",
    [
        (403, 99991672, "permission_denied"),
        (400, 230027, "permission_denied"),
        (429, 0, "rate_limited"),
        (400, 230020, "rate_limited"),
        (401, 99991663, "authentication_failed"),
        (500, 1, "http_error"),
        (200, 12345, "api_error"),
    ],
)
def test_failures_do_not_echo_server_messages_or_retry(status, code, kind):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"code": code, "msg": "PRIVATE_RESPONSE_TEXT"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProbeError) as error:
            probe(client, "chats")
    assert str(error.value) == kind
    assert "PRIVATE" not in str(error.value)
    assert len(calls) == 1


def test_history_reads_one_page_and_never_returns_message_content():
    result, calls = run(
        [page([{"body": {"content": "PRIVATE_MESSAGE"}}], True, "next")],
        operation="history",
        max_pages=1,
    )
    assert result["complete"] is False
    assert result["sample_only"] is True
    assert result["pages"] == 1
    assert len(calls) == 1
    assert "PRIVATE" not in json.dumps(result)


def test_dm_is_only_request_construction():
    plan = dry_run("dm", user_id="ou_fixture")
    assert plan["method"] == "POST"
    assert plan["params"] == {"receive_id_type": "open_id"}
    assert plan["json"]["receive_id"] == "ou_fixture"
    assert isinstance(plan["json"]["content"], str)
    assert plan["live_get_supported"] is False
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("network"))) as client:
        with pytest.raises(ProbeError, match="dry_run_only"):
            probe(client, "dm")


def test_default_cli_does_not_create_client_or_prompt(monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda _: pytest.fail("credential prompt"))
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("client created"))
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


@pytest.mark.parametrize("identifier", ["../other", "oc_x/y", "oc_x?x=y", "oc_x\n", ""])
def test_chat_identifier_cannot_change_path(identifier):
    with pytest.raises(ProbeError, match="invalid_chat_id"):
        dry_run("members", chat_id=identifier)


def test_redirect_is_not_followed_and_transport_error_is_redacted():
    for response in [httpx.Response(302, headers={"location": "https://example.invalid"}), None]:
        calls = []

        def handle(request):
            calls.append(request)
            if response is None:
                raise RuntimeError("PRIVATE_TRANSPORT_DETAIL")
            return response

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(ProbeError) as error:
                probe(client, "chats")
        assert str(error.value) == ("transport_error" if response is None else "redirect_refused")
        assert len(calls) == 1


def test_cli_never_executes_dm_or_accepts_piped_credentials(monkeypatch, capsys):
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("client created"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["--execute", "--operation", "dm", "--user-id", "ou_fixture"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "dry_run_only"}
    assert main(["--execute", "--plan-sha256", dry_run("chats")["plan_sha256"]]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "interactive_token_required"}


@pytest.mark.parametrize("budget", [True, 0, 21, -1])
def test_invalid_page_budget_never_requests(budget):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("network"))) as client:
        with pytest.raises(ProbeError, match="invalid_page_budget"):
            probe(client, "chats", max_pages=budget)


def test_hidden_input_failure_never_falls_back_to_echo(monkeypatch, capsys):
    import getpass
    import warnings

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("client created"))

    def unavailable_terminal(_prompt):
        warnings.warn("terminal unavailable", getpass.GetPassWarning)
        pytest.fail("echo fallback reached")

    monkeypatch.setattr(getpass, "getpass", unavailable_terminal)
    assert main(["--execute", "--plan-sha256", dry_run("chats")["plan_sha256"]]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "probe_failed"}


def test_plan_binds_request_budget_and_refuses_different_execute_plan(monkeypatch, capsys):
    plan = dry_run("chats", max_pages=2)
    assert plan["max_pages"] == 2
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("network"))
    assert main(["--execute", "--plan-sha256", plan["plan_sha256"]]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "plan_mismatch"}
    with pytest.raises(ProbeError, match="invalid_page_budget"):
        dry_run("chats", max_pages=0)
    with pytest.raises(ProbeError, match="invalid_page_budget"):
        dry_run("history", chat_id="oc_fixture", max_pages=2)


def test_exception_context_is_not_retained():
    for malformed in (False, True):

        def handle(_request):
            if malformed:
                return httpx.Response(200, text="PRIVATE_INVALID_JSON")
            raise RuntimeError("PRIVATE_DETAIL")

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(ProbeError) as error:
                probe(client, "chats")
        assert error.value.__cause__ is None
        assert error.value.__context__ is None


@pytest.mark.parametrize("status", [200, 400])
def test_execute_with_fake_transport_is_redacted_and_uses_safe_client(monkeypatch, capsys, status):
    import getpass

    original_client = httpx.Client
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(getpass, "getpass", lambda _: "synthetic-credential")

    def handle(request):
        assert request.headers["authorization"] == "Bearer synthetic-credential"
        return httpx.Response(
            status,
            json=page([{"name": "PRIVATE_MEMBER"}])
            if status == 200
            else {"code": 230027, "msg": "PRIVATE_ERROR"},
        )

    def client_factory(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    plan = dry_run("members", chat_id="oc_fixture")
    assert main(
        [
            "--execute",
            "--operation",
            "members",
            "--chat-id",
            "oc_fixture",
            "--plan-sha256",
            plan["plan_sha256"],
        ]
    ) == (0 if status == 200 else 1)
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result.get("items") == 1 if status == 200 else result == {"error": "permission_denied"}
    assert "synthetic-credential" not in captured.out + captured.err
    assert "PRIVATE" not in captured.out + captured.err


@pytest.mark.parametrize(
    "status,kind",
    [
        (429, "rate_limited"),
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (503, "http_error"),
        (200, "malformed_response"),
    ],
)
def test_non_json_response_categories(status, kind):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="PRIVATE_HTML"))
    ) as client:
        with pytest.raises(ProbeError, match=f"^{kind}$"):
            probe(client, "chats")


@pytest.mark.parametrize("token", ["", "with space", "非ASCII", "control\x00"])
def test_invalid_tokens_fail_before_creating_client(monkeypatch, capsys, token):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda _: token)
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("network"))
    assert main(["--execute", "--plan-sha256", dry_run("chats")["plan_sha256"]]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_token"}
