import importlib.util
from pathlib import Path

import pytest


def module(name):
    path = Path(__file__).resolve().parents[2] / "scripts/ci" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


m = module("macos_dispatch_reconcile")
lease_module = module("linux_dispatch_journal")
v = module("macos_vm_recovery")
KEY = "10-2-30"
NAME = "osvox-prod-" + "a" * 32


@pytest.fixture
def case(tmp_path):
    tmp_path.chmod(0o700)
    leases = lease_module.LeaseJournal(tmp_path / "leases")
    vms = v.VMJournal(tmp_path / "vms")
    leases.reserve(KEY, NAME)
    leases.registered(KEY, 42)
    vm = vms.reserve(KEY)
    vms.bind_instance(vm, "1:2")
    leases.launch_intent(KEY)
    state = {
        "vms": {vm: True, "unrelated": True}, "calls": [],
        "runner": {"id": 42, "name": NAME, "busy": False},
        "job": {"id": 30, "run_id": 10, "run_attempt": 2, "name": "swift-build",
                "status": "completed", "runner_id": 42, "runner_name": NAME},
    }

    def api(path, method="GET"):
        state["calls"].append((path, method))
        if state.get("api_down"):
            raise OSError("unavailable")
        if "/jobs/" in path:
            return dict(state["job"])
        if "/runner-groups/" in path:
            return {"runners": [state["runner"]] if state["runner"] else []}
        if method == "DELETE" and not state.get("deletion_lost"):
            state["runner"] = None
        return state["runner"]

    def stop(name):
        state["calls"].append(("stop", name))
        state["vms"][name] = False

    def delete(name):
        state["vms"].pop(name)

    def run():
        return m.reconcile_pending(
            leases, vms, api, list_vms=lambda: dict(state["vms"]), stop=stop,
            delete=delete, instance_identity=lambda name: state.get("identity", "1:2"),
            owned_processes_absent=lambda key: not state.get("process_remains", False),
            owned_workspace_absent=lambda key: not state.get("workspace_remains", False),
            vm_processes_absent=lambda name: not state.get("vm_process_remains", False),
            vm_workspace_absent=lambda name: not state.get("vm_workspace_remains", False),
        )
    yield leases, vms, state, run
    leases.close()
    vms.close()


def test_success_and_idempotence(case):
    leases, vms, state, run = case
    assert run() == [KEY]
    assert not leases.pending() and not vms.pending()
    assert state["vms"] == {"unrelated": True}
    assert state["calls"][0][0] == "stop"
    assert all("cancel" not in path for path, _ in state["calls"])
    assert run() == []


@pytest.mark.parametrize("condition", ["api_down", "deletion_lost", "process_remains", "workspace_remains"])
def test_failures_leave_lease_recoverable(case, condition):
    leases, vms, state, run = case
    state[condition] = True
    with pytest.raises((OSError, RuntimeError)):
        run()
    assert leases.pending() and not vms.pending()
    state[condition] = False
    assert run() == [KEY]


def test_vm_child_still_running_blocks_cleanup_and_lease_resolution(case):
    leases, vms, state, run = case
    state["vm_process_remains"] = True
    with pytest.raises(RuntimeError, match="process"):
        run()
    assert leases.pending() and vms.pending()
    assert state["runner"]
    assert not any(method == "DELETE" for _, method in state["calls"])


def test_vm_directory_absent_from_inventory_still_blocks_release(case):
    leases, vms, state, run = case
    state['vms'] = {'unrelated': True}
    state['vm_workspace_remains'] = True
    with pytest.raises(RuntimeError, match='workspace'):
        run()
    assert leases.pending() and vms.pending() and state['runner']


def test_nonterminal_stops_vm_but_preserves_runner(case):
    leases, _, state, run = case
    state["job"]["status"] = "in_progress"
    assert run() == []
    assert leases.pending() and state["runner"]
    assert not any(method == "DELETE" for _, method in state["calls"])


@pytest.mark.parametrize("field,value", [("id", 31), ("run_attempt", True), ("run_id", 11),
                                        ("name", "windows-tauri-test"), ("runner_id", 43),
                                        ("runner_name", "unrelated")])
def test_job_mismatch_never_deletes_runner(case, field, value):
    leases, _, state, run = case
    state["job"][field] = value
    with pytest.raises(ValueError):
        run()
    assert leases.pending() and state["runner"]
    assert not any(method == "DELETE" for _, method in state["calls"])


def test_replaced_vm_no_github_mutation(case):
    leases, _, state, run = case
    state["identity"] = "1:3"
    with pytest.raises(RuntimeError):
        run()
    assert leases.pending() and not state["calls"]


def test_registration_response_lost_absence_quarantines(case):
    leases, _, state, run = case
    leases.db.execute("UPDATE leases SET state='registration_unknown',runner_id=NULL")
    state["runner"] = None
    state["job"].update(runner_id=0, runner_name="")
    with pytest.raises(RuntimeError, match="Uncertain registration"):
        run()
    assert leases.pending()


def test_registration_response_lost_exact_runner_recovers(case):
    leases, _, state, run = case
    leases.db.execute("UPDATE leases SET state='registration_unknown',runner_id=NULL")
    state["job"].update(runner_id=0, runner_name="")
    assert run() == [KEY] and not leases.pending()


def test_busy_runner_quarantines(case):
    leases, _, state, run = case
    state["runner"]["busy"] = True
    with pytest.raises(ValueError):
        run()
    assert leases.pending()


def test_inventory_requires_complete_pagination():
    calls = []

    def api(path):
        calls.append(path)
        return {"runners": [{"id": i} for i in range(100)]}

    with pytest.raises(RuntimeError, match="Incomplete"):
        m.inventory(api)
    assert len(calls) == 10


def test_launched_lease_without_vm_record_quarantines(case):
    leases, vms, state, run = case
    vms.db.execute("DELETE FROM owned_vms")
    with pytest.raises(ValueError, match="durable VM absence"):
        run()
    assert leases.pending() and state["runner"]


@pytest.mark.parametrize("body", [None, {}, {"runners": None}, {"runners": [None]}])
def test_invalid_inventory_is_not_absence(body):
    with pytest.raises(ValueError):
        m.inventory(lambda path: body)


def test_vm_reappearing_after_cleanup_blocks_resolution(case):
    leases, vms, state, run = case
    state["api_down"] = True
    with pytest.raises(OSError):
        run()
    name = vms.db.execute("SELECT name FROM owned_vms").fetchone()[0]
    state["api_down"] = False
    state["vms"][name] = False
    with pytest.raises(RuntimeError, match="reappeared"):
        run()
    assert leases.pending()
