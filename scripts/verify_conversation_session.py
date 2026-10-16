#!/usr/bin/env python3
"""Verify P3.3 deterministic gates and emit a secret-free handoff sheet.

The generated evidence records only check names and statuses. Command output,
HTTP response bodies, conversation content, capabilities, participant/device
identifiers, and token digests are deliberately never serialized.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


Status = Literal["PASS", "FAIL", "PENDING", "SKIP"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    # Internal diagnostic only. build_evidence and console output intentionally
    # omit this field so a future probe cannot leak a response body or secret.
    detail: str


PUBLIC_INGRESS_ROUTES = frozenset(
    {
        ("POST", "/public/v1/conversation-sessions/claim"),
        ("GET", "/public/v1/conversation-sessions/:id/live"),
        ("POST", "/public/v1/conversation-sessions/:id/live"),
        ("GET", "/public/v1/conversation-sessions/:id/turns"),
        ("POST", "/public/v1/conversation-sessions/:id/turns"),
    }
)

MANUAL_CHECKS = (
    CheckResult("iOS camera QR claim", "PENDING", "named physical device required"),
    CheckResult("Android camera QR claim", "PENDING", "named physical device required"),
    CheckResult("iOS deep-link handoff", "PENDING", "named physical device required"),
    CheckResult("Android deep-link handoff", "PENDING", "named physical device required"),
    CheckResult("iOS background replay", "PENDING", "named physical device required"),
    CheckResult("Android background replay", "PENDING", "named physical device required"),
    CheckResult("Adverse-network recovery", "PENDING", "two physical devices required"),
    CheckResult("Live draft and final convergence", "PENDING", "two physical devices required"),
    CheckResult("Live draft loss and final replay recovery", "PENDING", "two physical devices required"),
    CheckResult("Invite/session expiry and host end", "PENDING", "two physical devices required"),
)


def normalize_engine_url(value: str) -> str:
    """Return a safe origin-only HTTP URL or raise ValueError."""

    parsed = urlsplit(value.strip())
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise ValueError("engine URL has an invalid port") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("engine URL must be an http(s) origin")
    if parsed.username or parsed.password:
        raise ValueError("engine URL must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("engine URL must not contain a path, query, or fragment")

    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    port = f":{parsed_port}" if parsed_port is not None else ""
    return f"{parsed.scheme}://{host}{port}"


def _run_command(name: str, command: list[str], *, cwd: Path) -> CheckResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(name, "FAIL", type(exc).__name__)
    status: Status = "PASS" if completed.returncode == 0 else "FAIL"
    return CheckResult(name, status, f"exit={completed.returncode}")


def check_public_ingress_contract(repo_root: Path) -> CheckResult:
    """Prove the gateway module defines only the approved public operations."""

    routes_dir = repo_root / "services" / "api-ts" / "src" / "routes"
    try:
        sources = [path.read_text(encoding="utf-8") for path in routes_dir.glob("*.ts")]
    except OSError as exc:
        return CheckResult("Public ingress route contract", "FAIL", type(exc).__name__)

    route_pattern = re.compile(
        r'''app\.(get|post|put|patch|delete)\(\s*["'`]'''
        r'''(/public/v1/conversation-sessions[^"'`]*)["'`]'''
    )
    matches = [
        (method.upper(), path)
        for source in sources
        for method, path in route_pattern.findall(source)
    ]
    actual = frozenset(matches)
    status: Status = (
        "PASS"
        if actual == PUBLIC_INGRESS_ROUTES and len(matches) == len(PUBLIC_INGRESS_ROUTES)
        else "FAIL"
    )
    return CheckResult(
        "Public ingress route contract",
        status,
        f"expected={len(PUBLIC_INGRESS_ROUTES)} actual={len(actual)}",
    )


def _probe_health(engine_url: str) -> CheckResult:
    """Probe health without reading or recording a response body."""

    try:
        with urlopen(f"{engine_url}/health", timeout=5) as response:  # noqa: S310
            status: Status = "PASS" if response.status == 200 else "FAIL"
            return CheckResult("Engine health", status, f"http={response.status}")
    except (HTTPError, URLError, TimeoutError) as exc:
        return CheckResult("Engine health", "FAIL", type(exc).__name__)


def _git_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "UNAVAILABLE"
    return result.stdout.strip() if result.returncode == 0 else "UNAVAILABLE"


def build_evidence(
    *,
    automated: list[CheckResult],
    generated_at: str | None = None,
    commit: str = "UNAVAILABLE",
) -> str:
    """Render status-only evidence; diagnostic detail is never included."""

    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    rows = "\n".join(
        f"| {result.name} | {result.status} |" for result in (*automated, *MANUAL_CHECKS)
    )
    return f"""# Vox Agent P3.3 conversation-session evidence

- Generated at (UTC): `{timestamp}`
- App commit: `{commit}`
- Evidence policy: status-only; no content, credentials, participant/device identifiers, or digests

## Results

| Check | Status |
| --- | --- |
{rows}

## Physical-device artifact references

- iOS evidence reference: `PENDING`
- Android evidence reference: `PENDING`
- Linked failure issues: `PENDING`

Do not promote a physical row without named-device evidence from the exact
commit/build. Store only redacted artifact references here; keep source audio,
conversation text, QR images, capabilities, serial numbers, and account/device
identifiers out of this file.
"""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine-url",
        help="optional private/trusted engine origin for a health probe",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path("build/p3.3-evidence/latest.md"),
    )
    parser.add_argument("--skip-gates", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    engine_url: str | None = None
    if args.engine_url:
        try:
            engine_url = normalize_engine_url(args.engine_url)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    repo_root = Path(__file__).resolve().parents[1]
    automated = [check_public_ingress_contract(repo_root)]
    if args.skip_gates:
        automated.extend(
            (
                CheckResult("Engine P3.3 tests", "SKIP", "operator requested skip"),
                CheckResult("Gateway P3.3 tests", "SKIP", "operator requested skip"),
                CheckResult("Mobile TypeScript", "SKIP", "operator requested skip"),
                CheckResult("Mobile component tests", "SKIP", "operator requested skip"),
            )
        )
    else:
        automated.extend(
            (
                _run_command(
                    "Engine P3.3 tests",
                    [sys.executable, "-m", "pytest", "tests/test_conversation_sessions.py", "-q"],
                    cwd=repo_root,
                ),
                _run_command(
                    "Gateway P3.3 tests",
                    ["npm", "run", "test", "--workspace", "services/api-ts", "--", "--test-name-pattern=conversation"],
                    cwd=repo_root,
                ),
                _run_command(
                    "Mobile TypeScript",
                    ["npm", "run", "typecheck"],
                    cwd=repo_root / "mobile" / "voxagent-companion",
                ),
                _run_command(
                    "Mobile component tests",
                    ["npm", "test"],
                    cwd=repo_root / "mobile" / "voxagent-companion",
                ),
            )
        )

    automated.append(
        _probe_health(engine_url)
        if engine_url
        else CheckResult("Engine health", "SKIP", "no origin supplied")
    )
    evidence = build_evidence(automated=automated, commit=_git_commit(repo_root))
    evidence_path = args.evidence.resolve()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(evidence, encoding="utf-8")
    print(f"wrote {evidence_path}")
    for result in automated:
        print(f"{result.status:7} {result.name}")
    return 1 if any(result.status == "FAIL" for result in automated) else 0


if __name__ == "__main__":
    raise SystemExit(main())
