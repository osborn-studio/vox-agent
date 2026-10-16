from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts/ci"))
from linux_dispatch_journal import LeaseJournal
from linux_dispatch_reconcile import reconcile_pending


def fixture(tmp_path, registered=True):
    tmp_path.chmod(0o700)
    journal = LeaseJournal(tmp_path / "leases.sqlite")
    name = "wiki-prod-" + "a" * 32
    journal.reserve("10-1-20", name)
    if registered:
        journal.registered("10-1-20", 30)
        journal.launch_intent("10-1-20")
    state = {"job": {"id": 20, "run_id": 10, "run_attempt": 1,
                     "status": "completed", "runner_id": 30 if registered else None,
                     "runner_name": name if registered else None},
             "runners": [{"id": 30, "name": name, "busy": False}], "deleted": []}

    def api(path, method="GET"):
        if path.endswith("/jobs/20"):
            return state["job"]
        if method == "DELETE":
            assert path == "orgs/osborn-studio/actions/runners/30"
            state["deleted"].append(30)
            state["runners"] = []
            return {}
        assert "/runner-groups/5/runners?" in path
        return {"runners": state["runners"]}

    def remote(namespace, nonce):
        assert namespace == "wiki"
        return {"status": "QUIESCENT_RECONCILED", "nonce": nonce,
                "root": "/var/lib/osborn-wiki-ci", "remote_processes_absent": True,
                "owned_workspace_absent": True, "recovery": {"workspaces_remaining": 0}}
    return journal, state, api, remote


@pytest.mark.parametrize("registered", [False, True])
def test_independent_proofs_resolve_registration_or_lease_crash(tmp_path, registered):
    j, state, api, remote = fixture(tmp_path, registered)
    try:
        assert reconcile_pending(j, api, remote) == ["10-1-20"]
        assert state["deleted"] == [30]
        assert j.pending() == []
    finally:
        j.close()


def test_live_job_never_calls_remote_or_deletes(tmp_path):
    j, state, api, _ = fixture(tmp_path)
    state["job"]["status"] = "in_progress"
    try:
        assert reconcile_pending(j, api, lambda *_: pytest.fail("Live job")) == []
        assert state["deleted"] == []
        assert j.pending()
    finally:
        j.close()


@pytest.mark.parametrize("case", ["nonce", "root", "process", "runner", "attempt"])
def test_mismatched_or_incomplete_proof_keeps_quarantine(tmp_path, case):
    j, state, api, remote = fixture(tmp_path)
    if case == "runner":
        state["runners"][0]["name"] = "wiki-prod-" + "b" * 32
    if case == "attempt":
        state["job"]["run_attempt"] = 2

    def bad_remote(namespace, nonce):
        proof = remote(namespace, nonce)
        if case == "nonce":
            proof["nonce"] = "a" * 32
        if case == "root":
            proof["root"] = "/var/lib/osvox-ci"
        if case == "process":
            proof["remote_processes_absent"] = False
        return proof

    try:
        with pytest.raises(ValueError):
            reconcile_pending(j, api, bad_remote)
        assert j.pending()
        assert state["deleted"] == []
    finally:
        j.close()


@pytest.mark.parametrize("assigned", [None, 0, False, 99])
def test_started_lease_wrong_or_missing_assignment_stays_quarantined(tmp_path, assigned):
    j, state, api, remote = fixture(tmp_path)
    state["job"]["runner_id"] = assigned
    with pytest.raises(ValueError, match="exact job assignment"):
        reconcile_pending(j, api, remote)
    assert j.pending() and not state["deleted"]
    j.close()


def test_started_lease_wrong_name_stays_quarantined(tmp_path):
    j, state, api, remote = fixture(tmp_path)
    state["job"]["runner_name"] = "other"
    with pytest.raises(ValueError, match="exact job assignment"):
        reconcile_pending(j, api, remote)
    assert j.pending() and not state["deleted"]
    j.close()


@pytest.mark.parametrize("assigned", [None, 0])
def test_registered_never_launched_cancellation_can_reconcile(tmp_path, assigned):
    j, state, api, remote = fixture(tmp_path, registered=False)
    j.registered("10-1-20", 30)
    state["job"]["runner_id"] = assigned
    assert reconcile_pending(j, api, remote) == ["10-1-20"]
    j.close()
