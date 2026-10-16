import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts/ci"))
import linux_dispatch_service as service
from linux_dispatch_journal import LeaseJournal


def journal(tmp_path):
    tmp_path.chmod(0o700)
    return LeaseJournal(tmp_path / "journal.sqlite")


def test_disabled_does_not_query_or_start(tmp_path):
    j = journal(tmp_path)
    request = Mock()
    discover = Mock()
    lease = Mock()
    assert service.cycle({}, j, request, Mock(), discover, lease) == "DISABLED"
    request.assert_not_called()
    discover.assert_not_called()
    lease.assert_not_called()
    j.close()


def test_active_lease_quarantines_other_jobs(tmp_path, monkeypatch):
    j = journal(tmp_path)
    j.reserve("1-1-1", "osvox-prod-" + "a" * 32)
    monkeypatch.setattr(service, "reconcile_pending", lambda *args: [])
    discover = Mock()
    lease = Mock()
    assert (
        service.cycle({"routing_approved": True}, j, Mock(), Mock(), discover, lease)
        == "QUARANTINED"
    )
    discover.assert_not_called()
    lease.assert_not_called()
    j.close()


def test_unknown_registration_is_durable_and_not_retried(tmp_path, monkeypatch):
    j = journal(tmp_path)
    monkeypatch.setattr(service, "admit", lambda *args: (True, "admitted"))
    monkeypatch.setattr(service, "verify_runner_group", lambda _: None)
    responses = [
        {"head_sha": "a" * 40, "run_attempt": 1},
        {"labels": ["bound"]},
        {"content": "eA=="},
    ]

    def request(path, **kwargs):
        if kwargs.get("method") == "POST":
            raise RuntimeError("unknown external outcome")
        return responses.pop(0)

    discover = Mock(return_value={"jobs": [{"admitted": True, "key": "1-1-1"}]})
    with pytest.raises(RuntimeError):
        service.cycle({"routing_approved": True}, j, request, Mock(), discover, Mock())
    assert len(j.pending()) == 1 and j.pending()[0][1] == "registration_unknown"
    j.close()
    restored = journal(tmp_path)
    with pytest.raises(RuntimeError, match="Unresolved lease"):
        restored.reserve("2-1-2", "wiki-prod-" + "b" * 32)
    restored.close()


def test_revalidate_stale_job_before_registration(tmp_path, monkeypatch):
    j = journal(tmp_path)
    monkeypatch.setattr(service, "admit", lambda *args: (False, "stale_job"))
    request = Mock(side_effect=[{"head_sha": "a" * 40}, {}, {"content": "eA=="}])
    lease = Mock()
    assert (
        service.cycle(
            {"routing_approved": True},
            j,
            request,
            Mock(),
            Mock(return_value={"jobs": [{"admitted": True, "key": "1-1-1"}]}),
            lease,
        )
        == "IDLE"
    )
    assert not j.pending()
    lease.assert_not_called()
    j.close()


@pytest.mark.parametrize("bad", ["workflow", "repository", "enabled"])
def test_group_policy_blocks_registration(tmp_path, monkeypatch, bad):
    j = journal(tmp_path)
    monkeypatch.setattr(service, "admit", lambda *args: (True, "admitted"))
    group = {"id": 4, "visibility": "selected", "restricted_to_workflows": True,
             "selected_workflows": [f"{service.REPOSITORY}/{service.WORKFLOW}@refs/heads/main"]}
    repos = {"total_count": 1, "repositories": [{"id": service.REPOSITORY_ID, "full_name": service.REPOSITORY}]}
    if bad == "workflow":
        group["selected_workflows"] = []
    if bad == "enabled":
        group["restricted_to_workflows"] = False
    if bad == "repository":
        repos["total_count"] = 2
    request = Mock(side_effect=[{"head_sha": "a"*40, "run_attempt": 1}, {"labels": []}, {"content": "eA=="}, group, repos])
    lease = Mock()
    with pytest.raises(RuntimeError, match="Runner group"):
        service.cycle({"routing_approved": True}, j, request, Mock(),
                      Mock(return_value={"jobs": [{"admitted": True, "key": "1-1-1"}]}), lease)
    assert not j.pending()
    lease.assert_not_called()
    j.close()


def test_exact_group_policy_passes():
    request = Mock(side_effect=[
        {"id": 4, "visibility": "selected", "restricted_to_workflows": True,
         "selected_workflows": [f"{service.REPOSITORY}/{service.WORKFLOW}@refs/heads/main"]},
        {"total_count": 1, "repositories": [{"id": service.REPOSITORY_ID, "full_name": service.REPOSITORY}]},
    ])
    service.verify_runner_group(request)


def test_launch_intent_survives_restart(tmp_path):
    j = journal(tmp_path)
    j.reserve("1-1-1", "osvox-prod-" + "a"*32)
    j.registered("1-1-1", 30)
    j.launch_intent("1-1-1")
    j.close()
    restored = journal(tmp_path)
    assert restored.pending()[0][1] == "lease_unknown"
    restored.close()


def test_ssh_sees_durable_launch_intent_and_failure_stays_quarantined(tmp_path, monkeypatch):
    j = journal(tmp_path)
    monkeypatch.setattr(service, "admit", lambda *args: (True, "admitted"))
    monkeypatch.setattr(service, "verify_runner_group", lambda _: None)
    request = Mock(side_effect=[
        {"head_sha": "a"*40, "run_attempt": 1}, {"labels": []}, {"content": "eA=="},
        {"runner": {"id": 30}, "encoded_jit_config": "synthetic"},
    ])

    def lease(*args):
        restored = journal(tmp_path)
        try:
            assert restored.pending()[0][1] == "lease_unknown"
        finally:
            restored.close()
        raise RuntimeError("SSH outcome unknown")

    with pytest.raises(RuntimeError, match="SSH outcome unknown"):
        service.cycle({"routing_approved": True}, j, request, Mock(),
                      Mock(return_value={"jobs": [{"admitted": True, "key": "1-1-1"}]}), lease)
    assert j.pending()[0][1] == "lease_unknown"
    j.close()
