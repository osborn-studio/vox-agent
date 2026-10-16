#!/usr/bin/env python3
"""Start a real engine the way the desktop shell does and check it admits only
its launcher (T29/T29b local verification).

The engine command is either a frozen executable or the source entrypoint:

    python scripts/verify_engine_loopback.py --engine apps/desktop-tauri/src-tauri/binaries/voxagent-engine-<triple>
    python scripts/verify_engine_loopback.py --source

It starts the engine on a free loopback port with an isolated data directory,
hands it a fresh secret on stdin (VOXAGENT_LOCAL_TOKEN_SOURCE=stdin), then
checks the handshake, refusals without a signature, a signed request, that a
replayed signature is refused, the WebSocket refusal and signed upgrade, that
the secret never appears in a response, and that a malformed secret makes the
engine refuse to start. The report never contains the secret.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, str, dict[str, str]]:
    request = urllib.request.Request(url, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8", "replace"), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace"), dict(error.headers)


def _sign(token: str, method: str, target: str) -> dict[str, str]:
    ts = int(time.time())
    nonce = secrets.token_hex(16)
    mac = hmac.new(token.encode(), f"voxagent-local-request-v1|{ts}|{nonce}|{method}|{target}".encode(),
                   hashlib.sha256).hexdigest()
    return {"X-VoxAgent-Local-Auth": f"v1.{ts}.{nonce}.{mac}"}


def _websocket_status(port: int, headers: dict[str, str] | None = None) -> str:
    """Status line of a raw WebSocket upgrade to /ws."""
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall((f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                      f"{extra}\r\n").encode())
        return sock.recv(256).decode("latin-1").split("\r\n", 1)[0]


def _env(data: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("VOXAGENT_")}
    # Source runs import this checkout's engine, not an installed copy.
    env["PYTHONPATH"] = str(ROOT)
    env.update({"VOXAGENT_LOCAL_TOKEN_SOURCE": "stdin", "VOXAGENT_DATA_DIR": str(data),
                "VOXAGENT_VOICE_MEMORY_DB": str(data / "voice_memory.db"), "PYTHONDONTWRITEBYTECODE": "1"})
    return env


EXPECTED_CHECKS = ("handshake_proof", "health_without_auth_401", "tasks_without_auth_401",
                   "health_signed_200", "replayed_signature_refused", "websocket_without_auth_refused",
                   "websocket_signed_accepted", "secret_not_in_responses", "stopped",
                   "bad_secret_refuses_to_start")


def run(engine: list[str], timeout: float = 60, token: str | None = None) -> dict:
    """Run the checks. `token` is for tests; normally a fresh one is generated."""
    checks: dict[str, bool] = {}
    report: dict = {"engine": [Path(engine[0]).name] + [Path(a).name for a in engine[1:]], "checks": checks}
    token = token or secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        log = (temp_path / "engine.log").open("w")
        process = subprocess.Popen(engine + ["--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                                   cwd=ROOT, env=_env(temp_path / "data"), stdin=subprocess.PIPE,
                                   stdout=log, stderr=log)
        try:
            process.stdin.write(token.encode() + b"\n")
            process.stdin.close()
            challenge = secrets.token_hex(16)
            deadline = time.monotonic() + timeout
            answer = None
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    status, body, _ = _get(f"{base}/v1/local/handshake", {"X-VoxAgent-Challenge": challenge})
                    if status == 200:
                        answer = json.loads(body)
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.25)
            expected = hmac.new(token.encode(), f"voxagent-local-handshake-v1|{challenge}".encode(),
                                hashlib.sha256).hexdigest()
            checks["handshake_proof"] = bool(answer) and hmac.compare_digest(answer.get("proof", ""), expected)
            report["engine_version"] = (answer or {}).get("version")
            responses = []

            def record(result):
                responses.append(result[1] + json.dumps(result[2]))
                return result

            checks["health_without_auth_401"] = record(_get(f"{base}/health"))[0] == 401
            checks["tasks_without_auth_401"] = record(_get(f"{base}/v1/tasks"))[0] == 401
            signed = _sign(token, "GET", "/health")
            status, body, _ = record(_get(f"{base}/health", signed))
            checks["health_signed_200"] = status == 200 and json.loads(body).get("status") == "ok"
            checks["replayed_signature_refused"] = record(_get(f"{base}/health", signed))[0] == 401
            checks["websocket_without_auth_refused"] = not _websocket_status(port).startswith("HTTP/1.1 101")
            checks["websocket_signed_accepted"] = _websocket_status(port, _sign(token, "GET", "/ws")).startswith(
                "HTTP/1.1 101")
            checks["secret_not_in_responses"] = all(token not in r for r in responses)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            log.close()
        checks["stopped"] = process.poll() is not None

        # Fail closed: a malformed secret on stdin stops the engine.
        failed = subprocess.run(engine + ["--host", "127.0.0.1", "--port", str(_free_port())], cwd=ROOT,
                                env=_env(temp_path / "data-bad"), input=b"short\n", capture_output=True,
                                timeout=timeout)
        checks["bad_secret_refuses_to_start"] = failed.returncode != 0
    report["status"] = "PASS" if all(checks.values()) and set(checks) == set(EXPECTED_CHECKS) else "FAIL"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--engine", type=Path, help="frozen engine executable")
    group.add_argument("--source", action="store_true", help="run scripts/frozen_engine.py with this Python")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    engine = [sys.executable, str(ROOT / "scripts/frozen_engine.py")] if args.source else [str(args.engine)]
    report = run(engine, timeout=args.timeout)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
