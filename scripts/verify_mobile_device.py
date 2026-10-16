#!/usr/bin/env python3
"""Run Mobile release preflight and create a real-device evidence sheet.

Automated checks can prove the TypeScript client, request contracts, engine
connectivity, and MCP device-tool registration. Native microphone behavior,
background transitions, and physical pairing remain explicitly pending until
an operator performs them on an identified device and attaches evidence.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


Status = Literal["PASS", "FAIL", "PENDING", "SKIP"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


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


def is_loopback_engine_url(engine_url: str) -> bool:
    host = urlsplit(engine_url).hostname or ""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _run_command(name: str, command: list[str], *, cwd: Path) -> CheckResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(name, "FAIL", f"could not run command: {type(exc).__name__}")
    if completed.returncode == 0:
        return CheckResult(name, "PASS", "command exited 0")
    return CheckResult(name, "FAIL", f"command exited {completed.returncode}")


def _probe_health(engine_url: str) -> CheckResult:
    try:
        with urlopen(f"{engine_url}/health", timeout=5) as response:  # noqa: S310
            payload = json.load(response)
            if response.status == 200 and payload.get("status") == "ok":
                return CheckResult(
                    "Engine health over device-reachable URL", "PASS", "HTTP 200; status=ok"
                )
            return CheckResult(
                "Engine health over device-reachable URL",
                "FAIL",
                f"HTTP {response.status}; unexpected health payload",
            )
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return CheckResult(
            "Engine health over device-reachable URL",
            "FAIL",
            f"probe failed: {type(exc).__name__}",
        )


def _probe_pairing_contract(engine_url: str) -> CheckResult:
    request = Request(f"{engine_url}/v1/devices/pairing-codes", method="POST")
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310
            payload = json.load(response)
        required = {"code", "qrPayload", "expiresAt"}
        if response.status == 200 and required.issubset(payload):
            return CheckResult(
                "Pairing-code API contract",
                "PASS",
                "created one transient code; secret value omitted",
            )
        return CheckResult("Pairing-code API contract", "FAIL", "response shape was incomplete")
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return CheckResult(
            "Pairing-code API contract",
            "FAIL",
            f"probe failed: {type(exc).__name__}",
        )


def _check_mcp_contract(repo_root: Path) -> CheckResult:
    source = (repo_root / "voxagent_mcp" / "voxagent_server.py").read_text(encoding="utf-8")
    required = ("vox.list_devices", "vox.invoke_device_tool")
    if all(tool in source for tool in required):
        return CheckResult("MCP device tool registration", "PASS", ", ".join(required))
    return CheckResult("MCP device tool registration", "FAIL", "required device tools missing")


MANUAL_CHECKS = (
    CheckResult(
        "Physical-device app launch",
        "PENDING",
        "Attach device model, OS/build version, app log, and screenshot.",
    ),
    CheckResult(
        "Microphone permission deny/retry",
        "PENDING",
        "Deny once, verify recovery copy, grant in Settings, then retry.",
    ),
    CheckResult(
        "Microphone capture and upload",
        "PENDING",
        "Record >=10 s; attach session ID, non-zero byte count, and server log.",
    ),
    CheckResult(
        "Sherpa processing and transcript",
        "PENDING",
        "Process uploaded session; attach timestamped transcript/segment evidence.",
    ),
    CheckResult(
        "Background and resume",
        "PENDING",
        "Exercise idle, recording, and uploading transitions; record observed state.",
    ),
    CheckResult(
        "Network failure and recovery",
        "PENDING",
        "Disable network during upload, verify offline state, restore, and retry successfully.",
    ),
    CheckResult(
        "MCP hardware flow",
        "PENDING",
        "Claim QR code, list device through MCP, invoke a device tool, and attach audit/session IDs.",
    ),
)


def build_evidence(
    *,
    operator: str,
    platform: str,
    device_model: str,
    engine_url: str,
    automated: list[CheckResult],
    generated_at: str | None = None,
) -> str:
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    rows = [*automated, *MANUAL_CHECKS]
    table = "\n".join(f"| {result.name} | {result.status} | {result.detail} |" for result in rows)
    return f"""# VOXAGENT Mobile real-device evidence

- Generated at (UTC): `{timestamp}`
- Operator: `{operator}`
- Platform: `{platform}`
- Device model: `{device_model}`
- Engine origin: `{engine_url}`
- App commit: `PENDING_OPERATOR_ENTRY`

## Results

| Check | Status | Evidence / observation |
| --- | --- | --- |
{table}

## Attached artifacts

- Device/system information: `PENDING`
- App logs: `PENDING`
- Engine logs: `PENDING`
- Screenshots or screen recording: `PENDING`
- Capture session, audit, and MCP invocation IDs: `PENDING`

## Operator attestation

Do not change `PENDING` to `PASS` without attached evidence from the identified
device and build. Record failures verbatim and link the follow-up issue before
sign-off.

- Operator/signature: `PENDING`
- Verification time (UTC): `PENDING`
- Release decision: `PENDING`
"""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-url", required=True, help="LAN-reachable engine origin")
    parser.add_argument("--platform", required=True, choices=("iOS", "Android", "simulator"))
    parser.add_argument("--device-model", default="unrecorded")
    parser.add_argument("--operator", default="unrecorded")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path("build/mobile-evidence/latest.md"),
    )
    parser.add_argument("--skip-mobile-gates", action="store_true")
    parser.add_argument(
        "--exercise-pairing",
        action="store_true",
        help="create one transient pairing code and validate its response shape",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        engine_url = normalize_engine_url(args.engine_url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    automated: list[CheckResult] = []
    if args.platform != "simulator" and is_loopback_engine_url(engine_url):
        automated.append(
            CheckResult(
                "Physical-device network address",
                "FAIL",
                "loopback points at the phone; use the engine host LAN address",
            )
        )
    else:
        automated.append(
            CheckResult("Physical-device network address", "PASS", "non-loopback origin")
        )

    if args.skip_mobile_gates:
        automated.extend(
            [
                CheckResult("Mobile TypeScript", "SKIP", "operator requested skip"),
                CheckResult("Mobile unit tests", "SKIP", "operator requested skip"),
            ]
        )
    else:
        mobile_dir = repo_root / "mobile" / "voxagent-companion"
        automated.extend(
            [
                _run_command("Mobile TypeScript", ["npm", "run", "typecheck"], cwd=mobile_dir),
                _run_command("Mobile unit tests", ["npm", "test"], cwd=mobile_dir),
            ]
        )

    automated.append(_probe_health(engine_url))
    automated.append(_check_mcp_contract(repo_root))
    automated.append(
        _probe_pairing_contract(engine_url)
        if args.exercise_pairing
        else CheckResult("Pairing-code API contract", "PENDING", "rerun with --exercise-pairing")
    )

    evidence = build_evidence(
        operator=args.operator,
        platform=args.platform,
        device_model=args.device_model,
        engine_url=engine_url,
        automated=automated,
    )
    evidence_path = args.evidence.resolve()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(evidence, encoding="utf-8")
    print(f"wrote {evidence_path}")
    for result in automated:
        print(f"{result.status:7} {result.name}: {result.detail}")
    return 1 if any(result.status == "FAIL" for result in automated) else 0


if __name__ == "__main__":
    raise SystemExit(main())
