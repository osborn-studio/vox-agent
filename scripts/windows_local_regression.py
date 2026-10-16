#!/usr/bin/env python3
"""Windows 4.2.8 local contract and security regression (T44a).

Runs every check of the preview's local scope and writes one JSON report:
which command ran, its exit code, duration, a hash of its output and only
the count lines of that output (never test names, paths or values). Each
check names the T44a dependency tasks it covers; what cannot run locally is
listed as an explicit exclusion with the place it is verified instead.

The verdict is at most LOCAL_SCOPE_PASS. `productPass` is always false: a
local run is not a Windows, device, release or production pass.

    python scripts/windows_local_regression.py --report build/windows-v4.2.8/evidence/T44a-report.json
    python scripts/windows_local_regression.py --list
    python scripts/windows_local_regression.py --only rust.test --only web.node
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OVERLAY = REPO_ROOT / "docs/plans/2026-10-08-windows-v4.2.8-task-catalog.overlay-fable.json"
MANIFEST = "apps/desktop-tauri/src-tauri/Cargo.toml"
SCHEMA = "voxagent.windows-local-regression"
STATEMENT = (
    "Local scope only: contracts, Rust, WebView code and leak scan on this host. "
    "This is not a full product pass; see exclusions."
)


@dataclass(frozen=True)
class Check:
    id: str
    command: tuple[str, ...]
    cwd: str
    covers: tuple[str, ...]


@dataclass(frozen=True)
class Exclusion:
    id: str
    covers: tuple[str, ...]
    reason: str
    verified_by: str


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    exit_code: int
    seconds: float
    summary: str
    output_sha256: str


CHECKS: tuple[Check, ...] = (
    Check("contracts.cloud", ("{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                              "tests/test_context_wire_fixture_v2.py",
                              "tests/test_context_extended_wire_fixture.py",
                              "tests/test_backup_idempotency_retention.py",
                              "tests/test_windows_mainline.py"),
          ".", ("T03", "T03b")),
    Check("python.full", ("{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider"), ".", ("T03", "T03b")),
    Check("python.ruff", ("{ruff}", "check", "."), ".", ()),
    Check("rust.fmt", ("cargo", "fmt", "--manifest-path", MANIFEST, "--all", "--", "--check"), ".", ()),
    Check("rust.clippy", ("cargo", "clippy", "--manifest-path", MANIFEST, "--locked", "--all-targets",
                          "--", "-D", "warnings"), ".", ()),
    Check("rust.test", ("cargo", "test", "--manifest-path", MANIFEST, "--locked"), ".",
          ("T04", "T05", "T06", "T06a", "T07a", "T08", "T09", "T09d", "T10", "T10b", "T17", "T23a")),
    Check("web.node", ("node", "--test", "test/*.test.mjs"), "apps/desktop-tauri",
          ("T10b", "T12", "T13", "T14", "T17", "T23a")),
    Check("security.plaintext_leaks", ("{python}", "scripts/scan_plaintext_leaks.py", "--cargo-flow"), ".",
          ("T44c", "T08", "T17")),
    Check("scripts.unittest", ("{python}", "-m", "unittest", "-q"), "scripts", ("T44c",)),
)

EXCLUSIONS: tuple[Exclusion, ...] = (
    Exclusion("windows-credential-store", ("T06a",),
              "T06a needs ci_windows_verified: the real Windows Credential Manager across processes",
              "ci_windows workflow_dispatch run (owner decision D3)"),
    Exclusion("dpapi-key-provider", ("T07b",),
              "DPAPI sealing and a second Windows profile need a Windows device",
              "device day T07b"),
    Exclusion("rust-advisory-audit", (),
              "the pinned cargo-audit gate needs the pinned advisory-db checkout",
              "CI rust audit gate (security/rust-audit-policy.json)"),
    Exclusion("webview2-shell", (),
              "the Tauri shell and WebView2 are not started; UI checks run on module and page code",
              "T41p preview install on a Windows device"),
    Exclusion("accessibility-device", ("T40b",),
              "Narrator, high contrast themes and per-monitor DPI",
              "docs/verification/windows-t40b-accessibility-device.md"),
    Exclusion("production-activation", ("T49p",),
              "real Feishu sign-in, dispatch and readback need L5 authorization",
              "T49p"),
    Exclusion("claude-e2e", (),
              "the external Claude CLI end-to-end test needs explicit authentication",
              "opt-in run, never counted here"),
)

# Each pattern matches only fixed words and numbers; the summary is rebuilt
# from the matches, so no other text of a line (names, paths, values) is kept.
_SUMMARY = re.compile(
    r"\b\d+ (?:passed|failed|skipped|errors?|warnings?)\b"
    r"|test result: (?:ok|FAILED)\. \d+ passed; \d+ failed"
    r"|^ℹ (?:tests|pass|fail|skipped) \d+"
    r"|^Ran \d+ tests?"
    r"|^(?:OK|FAILED)\b"
    r"|All checks passed!"
    r"|\"hits\": (?:\d+|\[\])"
    r"|\"scanned_files\": \d+"
)


def summarize(output: str) -> str:
    """Count fragments only; everything else may hold paths or values."""
    lines = []
    for line in output.splitlines():
        found = [m.group(0) for m in _SUMMARY.finditer(line.strip())]
        if found:
            lines.append(", ".join(found))
    return "\n".join(lines[-40:])


def dependencies() -> list[str]:
    catalog = json.loads(OVERLAY.read_text(encoding="utf-8"))
    task = next(t for t in catalog["tasks"] if t["id"] == "T44a")
    return list(task["dependencies"])


def uncovered(tasks: list[str]) -> list[str]:
    covered = {task for check in CHECKS for task in check.covers}
    covered |= {task for item in EXCLUSIONS for task in item.covers}
    return [task for task in tasks if task not in covered]


def _resolve(part: str) -> str:
    if part == "{python}":
        return sys.executable
    if part == "{ruff}":
        sibling = Path(sys.executable).with_name("ruff")
        return str(sibling) if sibling.exists() else (shutil.which("ruff") or "ruff")
    return part


def run_check(check: Check) -> CheckResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [_resolve(part) for part in check.command],
            cwd=REPO_ROOT / check.cwd,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        exit_code, output = completed.returncode, completed.stdout + completed.stderr
    except OSError:
        exit_code, output = 127, ""
    return CheckResult(
        check_id=check.id,
        exit_code=exit_code,
        seconds=round(time.monotonic() - started, 1),
        summary=summarize(output),
        output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
    )


_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def build_report(results: list[CheckResult], source_commit: str | None, tree_dirty: bool | None,
                 tree_changed: bool = False) -> dict:
    known = {check.id: check for check in CHECKS}
    for item in results:
        if item.check_id not in known:
            raise ValueError(f"unknown check {item.check_id}")
    ran = {item.check_id for item in results}
    not_run = [check.id for check in CHECKS if check.id not in ran]
    if any(item.exit_code != 0 for item in results):
        verdict = "FAIL"
    elif (not_run or tree_dirty is not False or tree_changed
          or not (source_commit and _COMMIT.match(source_commit))):
        verdict = "PARTIAL"
    else:
        verdict = "LOCAL_SCOPE_PASS"
    return {
        "schema": SCHEMA,
        "schemaVersion": 1,
        "taskId": "T44a",
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sourceCommit": source_commit,
        "treeDirty": tree_dirty,
        "treeChangedDuringRun": tree_changed,
        "host": {"os": platform.system(), "release": platform.release(), "arch": platform.machine()},
        "verdict": verdict,
        "productPass": False,
        "statement": STATEMENT,
        "checks": [
            {
                "id": item.check_id,
                "command": list(known[item.check_id].command),
                "cwd": known[item.check_id].cwd,
                "covers": list(known[item.check_id].covers),
                "exitCode": item.exit_code,
                "seconds": item.seconds,
                "summary": item.summary,
                "outputSha256": item.output_sha256,
            }
            for item in results
        ],
        "notRun": not_run,
        "exclusions": [
            {"id": e.id, "covers": list(e.covers), "reason": e.reason, "verifiedBy": e.verified_by}
            for e in EXCLUSIONS
        ],
    }


def _git(*args: str) -> str | None:
    """Git output, or None when git failed (an unknown state never passes)."""
    try:
        done = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def tree_state() -> tuple[str | None, str | None]:
    """(HEAD commit, porcelain status) or None for whatever git could not report."""
    return _git("rev-parse", "HEAD"), _git("status", "--porcelain")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    parser.add_argument("--only", action="append", default=[], help="run only this check id (repeatable)")
    parser.add_argument("--list", action="store_true", help="list checks and exclusions, run nothing")
    args = parser.parse_args(argv)

    missing = uncovered(dependencies())
    if missing:
        print(f"T44a dependencies without a check or exclusion: {', '.join(missing)}", file=sys.stderr)
        return 2
    if args.list:
        for check in CHECKS:
            print(f"{check.id:28} covers {', '.join(check.covers) or '-'}")
        for item in EXCLUSIONS:
            print(f"excluded {item.id:19} {item.reason} -> {item.verified_by}")
        return 0
    unknown = set(args.only) - {check.id for check in CHECKS}
    if unknown:
        print(f"unknown check: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    commit, status_before = tree_state()
    results = []
    for check in CHECKS:
        if args.only and check.id not in args.only:
            continue
        print(f"running {check.id} …", file=sys.stderr, flush=True)
        results.append(run_check(check))
        print(f"  exit {results[-1].exit_code} in {results[-1].seconds}s", file=sys.stderr, flush=True)
    commit_after, status_after = tree_state()
    dirty = None if status_before is None else bool(status_before)
    changed = commit_after != commit or status_after != status_before
    report = build_report(results, commit, dirty, tree_changed=changed)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["verdict"] == "LOCAL_SCOPE_PASS" else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main())
