import importlib.util
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys

import pytest

SOURCE = Path(__file__).parents[2] / "scripts/ci/linux_dispatch_journal.py"
spec = importlib.util.spec_from_file_location("journal", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NAME = "osvox-prod-" + "a" * 32


@pytest.fixture
def journal(tmp_path):
    tmp_path.chmod(0o700)
    j = m.LeaseJournal(tmp_path / "leases.sqlite")
    yield j
    j.close()


def proof():
    return dict(
        lease_key="1-1-2",
        runner_id=7,
        runner_name=NAME,
        job_terminal=True,
        runner_absent=True,
        remote_processes_absent=True,
        owned_workspace_absent=True,
    )


def test_unknown_blocks_every_other_job(journal):
    journal.reserve("1-1-2", NAME)
    with pytest.raises(RuntimeError):
        journal.reserve("3-1-4", "osvox-prod-" + "b" * 32)
    journal.registered("1-1-2", 7)
    for flag in (
        "job_terminal",
        "runner_absent",
        "remote_processes_absent",
        "owned_workspace_absent",
    ):
        p = proof()
        p[flag] = False
        with pytest.raises(ValueError):
            journal.resolve("1-1-2", p)
    journal.resolve("1-1-2", proof())
    assert journal.pending() == []
    with pytest.raises(sqlite3.IntegrityError):
        journal.reserve("1-1-2", NAME)  # No duplicate retry after restart.
    journal.reserve("3-1-4", "osvox-prod-" + "b" * 32)


@pytest.mark.parametrize("registered", [False, True])
def test_real_controller_sigkill_and_restart_quarantines(tmp_path, registered):
    tmp_path.chmod(0o700)
    code = f"""import sys,time
sys.path.insert(0,{str(SOURCE.parent)!r})
from linux_dispatch_journal import LeaseJournal
j=LeaseJournal({str(tmp_path / "leases.sqlite")!r})
j.reserve('1-1-2',{NAME!r})
if {registered!r}: j.registered('1-1-2',7)
print('DURABLE',flush=True)
time.sleep(60)
"""
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "DURABLE"
        os.kill(p.pid, signal.SIGKILL)
        p.wait(timeout=5)
        j = m.LeaseJournal(tmp_path / "leases.sqlite")
        assert j.pending()[0][1] == ("registered_not_started" if registered else "registration_unknown")
        with pytest.raises(RuntimeError):
            j.reserve("3-1-4", "osvox-prod-" + "b" * 32)
        assert j.db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        j.close()
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=5)


def test_two_controllers_cannot_reserve_same_capacity(tmp_path):
    tmp_path.chmod(0o700)
    a = m.LeaseJournal(tmp_path / "leases.sqlite")
    b = m.LeaseJournal(tmp_path / "leases.sqlite")
    try:
        a.reserve("1-1-2", NAME)
        with pytest.raises(RuntimeError):
            b.reserve("3-1-4", "osvox-prod-" + "b" * 32)
    finally:
        a.close()
        b.close()


def test_cross_repository_capacity_stays_quarantined_after_restart(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "leases.sqlite"
    wiki_name = "wiki-prod-" + "c" * 32
    wiki = m.LeaseJournal(path)
    wiki.reserve("11-1-12", wiki_name)
    wiki.registered("11-1-12", 17)
    wiki.close()
    vox = m.LeaseJournal(path)
    try:
        with pytest.raises(RuntimeError):
            vox.reserve("1-1-2", NAME)
        p = proof()
        p.update(lease_key="11-1-12", runner_id=17, runner_name=wiki_name)
        vox.resolve("11-1-12", p)
        vox.reserve("1-1-2", NAME)
        assert vox.pending()[0][3] == NAME
    finally:
        vox.close()


def test_refuse_public_directory_or_symlink(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError):
        m.LeaseJournal(tmp_path / "bad")
    tmp_path.chmod(0o700)
    (tmp_path / "link").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError):
        m.LeaseJournal(tmp_path / "link")
