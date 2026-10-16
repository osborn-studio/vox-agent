import importlib.util
from pathlib import Path
import pytest

p = Path(__file__).resolve().parents[2] / "scripts/ci/macos_vm_recovery.py"
spec = importlib.util.spec_from_file_location("macos_vm_recovery", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def journal(tmp_path):
    tmp_path.chmod(0o700)
    j = m.VMJournal(tmp_path / "vm.sqlite")
    yield j
    j.close()


def test_intent_survives_reopen_and_blocks_duplicate(journal):
    name = journal.reserve("1-1-2")
    with pytest.raises(RuntimeError):
        journal.reserve("1-1-3")
    assert journal.pending()[0][0] == name
    with pytest.raises(Exception):
        journal.reserve("1-1-2")


def test_cleanup_only_owned_and_no_github_claim(journal):
    name = journal.reserve("1-1-2")
    journal.bind_instance(name, "1:2")
    v = {name: True, "personal": True, m.BASE: False}
    actions = []

    def stop(n):
        actions.append(("stop", n))
        v[n] = False

    def delete(n):
        actions.append(("delete", n))
        v.pop(n)

    r = journal.reconcile(lambda: dict(v), stop, delete, lambda n: "1:2",
                          processes_absent=lambda n: True, workspace_absent=lambda n: True)
    assert actions == [("stop", name), ("delete", name)] and v == {"personal": True, m.BASE: False}
    assert r[0]["github_lease_resolved"] is False and not journal.pending()
    # Never replay the same exact job even after VM cleanup.
    with pytest.raises(Exception):
        journal.reserve("1-1-2")


@pytest.mark.parametrize("phase", ["stop", "delete", "inventory"])
def test_uncertain_cleanup_keeps_slot(journal, phase):
    name = journal.reserve("1-1-2")
    journal.bind_instance(name, "1:2")

    def fail(*a):
        raise OSError("unavailable")

    v = {name: True}

    def inv():
        return dict(v)

    def stop(n):
        v[n] = False

    with pytest.raises((OSError, RuntimeError)):
        journal.reconcile(
            fail if phase == "inventory" else inv,
            fail if phase == "stop" else stop,
            fail,
            lambda n: "1:2",
        )
    assert journal.pending()


def test_crash_before_clone_reconciles_absence(journal):
    journal.reserve("1-1-2")
    assert journal.reconcile(
        lambda: {}, lambda n: pytest.fail(), lambda n: pytest.fail(), lambda n: "1:2",
        processes_absent=lambda n: True, workspace_absent=lambda n: True
    )[0]["vm_absent"]


def test_unknown_clone_process_must_exit_before_marking_absence(journal):
    name = journal.reserve("1-1-2")
    with pytest.raises(RuntimeError, match="process"):
        journal.reconcile(lambda: {}, lambda n: pytest.fail(), lambda n: pytest.fail(),
                          lambda n: "1:2", processes_absent=lambda n: False, workspace_absent=lambda n: True)
    assert journal.pending()[0][3] == "clone_unknown"
    # The late clone is still tracked and quarantines as unbound, never admitted.
    with pytest.raises(RuntimeError, match="Unbound"):
        journal.reconcile(lambda: {name: False}, lambda n: pytest.fail(), lambda n: pytest.fail(),
                          lambda n: "1:2", processes_absent=lambda n: True, workspace_absent=lambda n: True)
    assert journal.pending()


def test_missing_process_adapter_cannot_claim_absence(journal):
    journal.reserve("1-1-2")
    with pytest.raises(RuntimeError, match="process"):
        journal.reconcile(lambda: {}, lambda n: None, lambda n: None, lambda n: "1:2")
    assert journal.pending()


@pytest.mark.parametrize(
    "name,base", [("personal", m.BASE_DIGEST), ("osvox-probe-" + "a" * 32, "personal")]
)
def test_tampered_record_cannot_delete(journal, name, base):
    journal.db.execute(
        "INSERT INTO owned_vms VALUES (?,?,?,?,NULL)", (name, base, "1-1-2", "clone_unknown")
    )
    with pytest.raises(ValueError):
        journal.reconcile(
            lambda: pytest.fail(), lambda n: pytest.fail(), lambda n: pytest.fail(), lambda n: "1:2"
        )


def test_symlink_journal_rejected(tmp_path):
    tmp_path.chmod(0o700)
    target = tmp_path / "target"
    target.touch()
    (tmp_path / "db").symlink_to(target)
    with pytest.raises(OSError):
        m.VMJournal(tmp_path / "db")


def test_shared_lock_excludes_second_owner(tmp_path):
    with m.capacity_lock(tmp_path / "lock"):
        with pytest.raises(BlockingIOError):
            with m.capacity_lock(tmp_path / "lock"):
                pass
    with m.capacity_lock(tmp_path / "lock"):
        pass


@pytest.mark.parametrize("bind,identity", [(False, "1:2"), (True, "3:4")])
def test_unbound_or_replaced_instance_quarantined(journal, bind, identity):
    name = journal.reserve("1-1-2")
    if bind:
        journal.bind_instance(name, "1:2")
    with pytest.raises(RuntimeError):
        journal.reconcile(
            lambda: {name: False},
            lambda n: pytest.fail(),
            lambda n: pytest.fail(),
            lambda n: identity,
        )
    assert journal.pending()


@pytest.mark.parametrize("bad_call", [2, 3])
def test_late_malformed_inventory_quarantines(journal, bad_call):
    name = journal.reserve("1-1-2")
    journal.bind_instance(name, "1:2")
    calls = []

    def inv():
        calls.append(1)
        return {name: "true"} if len(calls) == bad_call else {name: False}

    with pytest.raises(ValueError):
        journal.reconcile(inv, lambda n: None, lambda n: None, lambda n: "1:2",
                          processes_absent=lambda n: True, workspace_absent=lambda n: True)
    assert journal.pending()


def test_malformed_persisted_identity_denied(journal):
    name = journal.reserve("1-1-2")
    journal.db.execute("UPDATE owned_vms SET state='owned',instance=NULL WHERE name=?", (name,))
    with pytest.raises(ValueError):
        journal.reconcile(
            lambda: {name: False}, lambda n: pytest.fail(), lambda n: pytest.fail(), lambda n: None
        )
    assert journal.pending()
