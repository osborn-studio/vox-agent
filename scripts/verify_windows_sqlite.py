#!/usr/bin/env python3
"""Produce secret-free evidence for Vox Agent SQLite locking on Windows."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import queue
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.models import Task  # noqa: E402
from engine.store import StoreManager  # noqa: E402

EVIDENCE_CONTRACT_VERSION = "2026-07-19.windows-sqlite.1"


def _lock_holder(data_dir: str, ready: Any, connected: Any, attempting: Any, results: Any, hold_seconds: float) -> None:
    store = StoreManager(Path(data_dir))
    try:
        store.connect()
        if not connected.wait(timeout=10):
            raise TimeoutError("contender did not connect")
        store._tasks_db.execute("BEGIN IMMEDIATE")
        store._tasks_db.execute(
            """INSERT INTO task (id, agent_id, title, created_at)
               VALUES ('win-lock-probe-a', 'probe', 'synthetic',
                       '2026-07-19T00:00:00Z')"""
        )
        ready.set()
        if not attempting.wait(timeout=10):
            raise TimeoutError("contender did not begin measured attempt")
        time.sleep(hold_seconds)
        store._tasks_db.commit()
        results.put({"role": "writer_a", "status": "passed"})
    except Exception as exc:
        ready.set()
        results.put(
            {"role": "writer_a", "status": "failed", "error_type": type(exc).__name__}
        )
    finally:
        store.close()


def _lock_contender(data_dir: str, ready: Any, connected: Any, attempting: Any, results: Any) -> None:
    store = StoreManager(Path(data_dir))
    try:
        store.connect()
        configured_timeout = store._tasks_db.execute("PRAGMA busy_timeout").fetchone()[0]
        if configured_timeout < 5000:
            raise RuntimeError("store busy timeout below policy")
        connected.set()
        if not ready.wait(timeout=10):
            raise TimeoutError("holder did not acquire lock")
        store._tasks_db.execute("PRAGMA busy_timeout=0")
        try:
            store._tasks_db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode & 0xFF != sqlite3.SQLITE_BUSY:
                raise
        else:
            store._tasks_db.rollback()
            raise RuntimeError("holder write lock was not observed")
        store._tasks_db.execute(f"PRAGMA busy_timeout={int(configured_timeout)}")
        started = time.monotonic()
        attempting.set()
        store.insert_task(Task(id="win-lock-probe-b", agent_id="probe", title="synthetic"))
        results.put({"role": "writer_b", "status": "passed", "initial_write_locked": True, "configured_timeout_ms": configured_timeout,
                     "wait_ms": max(0, round((time.monotonic() - started) * 1000))})
    except Exception as exc:
        results.put({"role": "writer_b", "status": "failed", "error_type": type(exc).__name__})
    finally:
        store.close()


def run_lock_probe(data_dir: Path, *, hold_seconds: float = 0.25) -> dict[str, Any]:
    """Hold one write lock while a spawned process proves bounded waiting."""

    if not 0.05 <= hold_seconds <= 2:
        raise ValueError("hold_seconds must be between 0.05 and 2")
    data_dir.mkdir(parents=True, exist_ok=False)
    initial = StoreManager(data_dir)
    initial.connect()
    initial.close()

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    connected = context.Event()
    attempting = context.Event()
    results = context.Queue()
    holder = context.Process(
        target=_lock_holder,
        args=(str(data_dir), ready, connected, attempting, results, hold_seconds),
    )
    contender = context.Process(
        target=_lock_contender,
        args=(str(data_dir), ready, connected, attempting, results),
    )
    parent_error = None
    timed_out = False
    try:
        holder.start()
        contender.start()
        deadline = time.monotonic() + 15
        for process in (holder, contender):
            process.join(timeout=max(0, deadline - time.monotonic()))
        timed_out = holder.is_alive() or contender.is_alive()
    except Exception as exc:
        parent_error = type(exc).__name__
    finally:
        for process in (holder, contender):
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)

    reports: list[dict[str, Any]] = []
    for _ in range(2):
        try:
            reports.append(results.get(timeout=1))
        except queue.Empty:
            break
    by_role = {report.get("role"): report for report in reports}
    holder_exitcode = holder.exitcode
    contender_exitcode = contender.exitcode
    results.close()
    results.join_thread()
    for process in (holder, contender):
        if not process.is_alive():
            process.close()

    try:
        with closing(sqlite3.connect(data_dir / "tasks.db", timeout=5)) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            record_count = connection.execute(
                "SELECT COUNT(*) FROM task WHERE id LIKE 'win-lock-probe-%'"
            ).fetchone()[0]
    except sqlite3.Error as exc:
        return {"status": "failed", "error_types": ([parent_error] if parent_error else []) + [type(exc).__name__],
                "journal_mode": "unknown", "busy_timeout_ms": 0, "integrity_check": "unknown",
                "records_expected": 2, "records_actual": 0, "contender_wait_ms": 0}

    contender_report = by_role.get("writer_b", {})
    passed = (
        parent_error is None
        and not timed_out
        and holder_exitcode == 0
        and contender_exitcode == 0
        and by_role.get("writer_a", {}).get("status") == "passed"
        and contender_report.get("status") == "passed"
        and contender_report.get("initial_write_locked") is True
        and hold_seconds * 1000 * 0.5 <= contender_report.get("wait_ms", 0) < 5000
        and journal_mode == "wal"
        and integrity == "ok"
        and record_count == 2
    )
    result: dict[str, Any] = {
        "status": "passed" if passed else "failed",
        "journal_mode": journal_mode,
        "busy_timeout_ms": contender_report.get("configured_timeout_ms", 0),
        "integrity_check": integrity,
        "records_expected": 2,
        "records_actual": record_count,
        "contender_wait_ms": contender_report.get("wait_ms", 0),
    }
    if not passed:
        result["error_types"] = ([parent_error] if parent_error else []) + sorted(
            {
                str(report["error_type"])
                for report in reports
                if report.get("error_type")
            }
        ) or (["ProcessTimeout"] if timed_out else ["ProbeFailed"])
    return result


def build_evidence(
    result: dict[str, Any],
    *,
    platform_name: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    platform = platform_name or sys.platform
    host_platform = "windows" if platform.startswith("win") else (
        "macos" if platform == "darwin" else platform
    )
    return {
        "contract_version": EVIDENCE_CONTRACT_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "host_platform": host_platform,
        "windows_host": host_platform == "windows",
        "probe": result,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write JSON evidence to this path")
    parser.add_argument("--work-dir", type=Path, help="parent directory for the temporary probe")
    parser.add_argument("--require-windows", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.work_dir is not None and not args.work_dir.is_dir():
        raise SystemExit("--work-dir must be an existing directory")
    try:
        with tempfile.TemporaryDirectory(prefix="voxagent-sqlite-", dir=args.work_dir) as temporary:
            result = run_lock_probe(Path(temporary) / "data")
    except OSError as exc:
        result = {"status": "failed", "error_types": [type(exc).__name__],
                  "journal_mode": "unknown", "busy_timeout_ms": 0, "integrity_check": "unknown",
                  "records_expected": 2, "records_actual": 0, "contender_wait_ms": 0}
    evidence = build_evidence(result)
    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")

    if result["status"] != "passed":
        return 4
    if args.require_windows and not evidence["windows_host"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
