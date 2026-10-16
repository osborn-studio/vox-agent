from __future__ import annotations

import json
import shutil
import time

from scripts.verify_windows_sqlite import build_evidence, run_lock_probe
from scripts.verify_windows_sqlite import _lock_contender as original_contender


def delayed_contender(*args):
    time.sleep(0.3)
    original_contender(*args)


def test_lock_probe_waits_for_slow_contender_before_hold_timer(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.verify_windows_sqlite._lock_contender", delayed_contender)
    result = run_lock_probe(tmp_path / "slow-contender", hold_seconds=0.15)
    assert result["status"] == "passed"
    assert result["contender_wait_ms"] >= 100
    assert result["records_actual"] == 2


def test_lock_probe_proves_waiting_writer_and_integrity(tmp_path) -> None:
    result = run_lock_probe(tmp_path / "probe", hold_seconds=0.15)

    assert result["status"] == "passed"
    assert result["journal_mode"] == "wal"
    assert result["busy_timeout_ms"] == 5000
    assert result["integrity_check"] == "ok"
    assert result["records_expected"] == result["records_actual"] == 2
    assert result["contender_wait_ms"] >= 100
    assert "probe" not in json.dumps(result)


def test_evidence_records_platform_without_paths_or_row_content() -> None:
    evidence = build_evidence(
        {
            "status": "passed",
            "journal_mode": "wal",
            "busy_timeout_ms": 5000,
            "integrity_check": "ok",
            "records_expected": 2,
            "records_actual": 2,
            "contender_wait_ms": 120,
        },
        platform_name="win32",
        generated_at="2026-07-19T00:00:00+00:00",
    )

    assert evidence["contract_version"] == "2026-07-19.windows-sqlite.1"
    assert evidence["windows_host"] is True
    assert evidence["host_platform"] == "windows"
    assert "path" not in json.dumps(evidence).lower()
    assert "holder" not in json.dumps(evidence).lower()


def test_lock_probe_releases_files_for_windows_cleanup(tmp_path) -> None:
    root = tmp_path / "cleanup"
    root.mkdir()

    assert run_lock_probe(root / "data", hold_seconds=0.05)["status"] == "passed"
    shutil.rmtree(root)

    assert not root.exists()


def test_contender_refuses_store_timeout_below_policy(monkeypatch, tmp_path):
    from queue import Queue
    from threading import Event
    from scripts import verify_windows_sqlite as probe

    class BadStore:
        def __init__(self, path):
            self._tasks_db = self
        def connect(self):
            pass
        def execute(self, sql):
            assert sql == 'PRAGMA busy_timeout'
            return self
        def fetchone(self):
            return (0,)
        def close(self):
            pass
    monkeypatch.setattr(probe, 'StoreManager', BadStore)
    ready, connected, attempting, results = Event(), Event(), Event(), Queue()
    probe._lock_contender(str(tmp_path), ready, connected, attempting, results)
    assert results.get_nowait()['status'] == 'failed'
    assert not connected.is_set() and not attempting.is_set()


def test_failed_probe_exit_takes_priority_over_platform(monkeypatch, capsys):
    from scripts import verify_windows_sqlite as probe
    monkeypatch.setattr(probe, 'run_lock_probe', lambda path: {'status': 'failed', 'error_types': ['Synthetic']})
    assert probe.main(['--require-windows']) == 4
    assert json.loads(capsys.readouterr().out)['probe']['status'] == 'failed'


def test_second_process_start_failure_reaps_first(monkeypatch, tmp_path):
    from queue import Queue
    from threading import Event
    from scripts import verify_windows_sqlite as probe
    processes = []
    class Results(Queue):
        def close(self):
            pass
        def join_thread(self):
            pass
    class Process:
        def __init__(self, **kwargs):
            self.pid = None
            self.exitcode = None
            self.alive = False
            self.closed = False
            processes.append(self)
        def start(self):
            if len(processes) == 2 and self is processes[1]:
                raise OSError('synthetic start failure')
            self.pid = 1
            self.alive = True
        def join(self, timeout):
            pass
        def is_alive(self):
            return self.alive
        def terminate(self):
            self.alive = False
            self.exitcode = -15
        def kill(self):
            self.alive = False
        def close(self):
            self.closed = True
    class Context:
        pass
    Context.Event = staticmethod(Event)
    Context.Queue = staticmethod(Results)
    Context.Process = staticmethod(Process)
    monkeypatch.setattr(probe.multiprocessing, 'get_context', lambda mode: Context())
    result = probe.run_lock_probe(tmp_path / 'start-failure')
    assert result['status'] == 'failed' and 'OSError' in result['error_types']
    assert all(p.closed and not p.alive for p in processes)
