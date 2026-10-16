"""The capability probe must never silently send or mistake a preview for evidence."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "probe", Path(__file__).parents[1] / "scripts/verify_feishu_backup.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_default_is_offline_preview():
    called = []
    result = probe.run_probe("oc_test", runner=lambda args: called.append(args))
    assert called == []
    assert result["status"] == "preview_only"
    assert result["verified"] is False


def test_live_needs_explicit_group():
    with pytest.raises(ValueError):
        probe.run_probe("", live=True)
    with pytest.raises(ValueError):
        probe.run_probe("ou_person", live=True)


def test_send_replay_and_readback():
    calls = []
    def runner(args):
        calls.append(args)
        if "+messages-send" in args:
            return {"message_id": "om_test", "chat_id": "oc_test"}
        text = calls[0][calls[0].index("--text") + 1]
        return {"messages": [{"message_id": "om_test", "content": text}], "has_more": False}
    result = probe.run_probe("oc_test", live=True, runner=runner)
    assert result["verified"]
    assert result["status"] == "passed"
    assert calls[0] == calls[1]
    assert all("--as" in c and "bot" in c for c in calls)
    assert "synthetic" in calls[0][calls[0].index("--text") + 1]


def test_missing_readback_is_not_success():
    def runner(args):
        if "+messages-send" in args:
            return {"message_id": "om_test", "chat_id": "oc_test"}
        return {"messages": [], "has_more": False}
    assert probe.run_probe("oc_test", live=True, runner=runner)["status"] == "readback_missing"


def test_changed_message_id_is_failed_idempotency():
    ids = iter(["om_first", "om_second"])
    def runner(args):
        return {"message_id": next(ids), "chat_id": "oc_test"}
    assert probe.run_probe("oc_test", live=True, runner=runner)["status"] == "idempotency_failed"


def test_business_error_is_sanitized():
    with pytest.raises(probe.ProbeError, match="cli_request_failed"):
        probe.unwrap({"ok": False, "error": {"message": "sensitive secret"}})


def test_wrapped_data_supported():
    assert probe.unwrap({"ok": True, "data": {"message_id": "om_a"}})["message_id"] == "om_a"


def test_pagination_is_bounded():
    count = 0
    def runner(args):
        nonlocal count
        if "+messages-send" in args:
            return {"message_id": "om_test", "chat_id": "oc_test"}
        count += 1
        return {"messages": [], "has_more": True, "page_token": str(count)}
    result = probe.run_probe("oc_test", live=True, runner=runner, max_pages=2)
    assert count == 2
    assert result["status"] == "readback_incomplete"


def test_cli_uses_argument_array_and_suppresses_raw_errors(monkeypatch):
    import subprocess
    def fail(args, **kwargs):
        assert isinstance(args, list)
        assert "shell" not in kwargs
        return subprocess.CompletedProcess(args, 1, "private conversation", "secret")
    monkeypatch.setattr(probe.subprocess, "run", fail)
    with pytest.raises(probe.ProbeError) as error:
        probe.cli(["im", "+messages-send"])
    assert "secret" not in str(error.value)
    assert "private" not in str(error.value)


def test_matching_id_but_wrong_text_is_failure():
    def runner(args):
        if "+messages-send" in args:
            return {"message_id": "om_test", "chat_id": "oc_test"}
        return {"messages": [{"message_id": "om_test", "content": "other"}]}
    assert probe.run_probe("oc_test", live=True, runner=runner)["status"] == "readback_mismatch"


def test_unknown_send_response_does_not_retry():
    calls = []
    def runner(args):
        calls.append(args)
        return {}
    with pytest.raises(probe.ProbeError, match="delivery_unknown"):
        probe.run_probe("oc_test", live=True, runner=runner)
    assert len(calls) == 1


def test_duplicate_with_same_receipt_is_detected():
    payload = None
    def runner(args):
        nonlocal payload
        if "+messages-send" in args:
            payload = args[args.index("--text") + 1]
            return {"message_id": "om_test", "chat_id": "oc_test"}
        return {"messages": [{"message_id": x, "content": payload}
                             for x in ["om_test", "om_duplicate"]], "has_more": False}
    assert probe.run_probe("oc_test", live=True, runner=runner)["status"] == "duplicate_messages"


def test_failure_preserves_reconciliation_identifiers():
    calls = 0
    def runner(args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"message_id": "om_test", "chat_id": "oc_test"}
        raise probe.ProbeError("delivery_unknown_timeout_do_not_blindly_repeat")
    with pytest.raises(probe.ProbeError) as error:
        probe.run_probe("oc_test", live=True, runner=runner)
    assert error.value.evidence["message_id"] == "om_test"
    assert error.value.evidence["event_id"]


def test_real_default_path_never_spawns_cli(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must stay offline")
    monkeypatch.setattr(probe.subprocess, "run", forbidden)
    assert probe.run_probe("oc_test")["status"] == "preview_only"
