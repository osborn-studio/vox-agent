#!/usr/bin/env python3
"""Build the self-contained voice backend the Windows desktop bundles (PR-B).

PyInstaller freezes ``scripts/frozen_backend.py`` (the compatibility voice
runtime, ``apps/desktop/server.py``) into
``apps/desktop-tauri/src-tauri/binaries/voxagent-backend-<target-triple>[.exe]``
for Tauri ``externalBin``. ``tauri build --config
src-tauri/tauri.release-engine.conf.json`` installs it beside
``Vox Agent.exe``; the shell launches it in bundled mode (``--require-auth``,
launch token and launch id on stdin) on loopback port 7902.

Only the cloud ASR and LLM routes are frozen in. Local ASR runtimes
(sherpa-onnx, whisper.cpp, Qwen local) are not bundled: those routes fail
closed with a visible error until a later PR packages them.

PyInstaller cannot cross-compile: build on the target OS. Needs
``pip install . pyinstaller`` in the chosen Python.

    python scripts/build_windows_backend.py              # host target
    python scripts/build_windows_backend.py --verify     # also run the authenticated loopback check
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_windows_engine import (  # noqa: E402
    BINARIES,
    ROOT,
    BuildError,
    arch_matches,
    binary_arch,
    check_target,
    host_target,
    python_machine,
    sha256,
    source_commit,
)
from verify_engine_loopback import stop_tree  # noqa: E402

ENTRYPOINT = ROOT / "scripts/frozen_backend.py"
NAME = "voxagent-backend"
COLLECT_ALL = ("fastapi", "uvicorn", "httpx", "structlog", "dotenv", "pydantic",
               "multipart", "openai", "dashscope")
# Lazily imported provider and routing modules are not visible to static analysis.
COLLECT_SUBMODULES = ("runtime", "apps.desktop", "memory", "integrations")
# runtime/models/config.py resolves the dictionaries next to the package root.
DATA = ("dictionaries",)


def bundle_name(target: str) -> str:
    return f"{NAME}-{target}" + (".exe" if "windows" in target else "")


def pyinstaller_command(python: str, work: Path, dist: Path) -> list[str]:
    command = [python, "-m", "PyInstaller", "--clean", "--noconfirm", "--onefile",
               "--distpath", str(dist), "--workpath", str(work / "work"),
               "--specpath", str(work / "spec"), "--paths", str(ROOT),
               "--name", NAME]
    for package in COLLECT_ALL:
        command += ["--collect-all", package]
    for package in COLLECT_SUBMODULES:
        command += ["--collect-submodules", package]
    for folder in DATA:
        command += ["--add-data", f"{ROOT / folder}{os.pathsep}{folder}"]
    return command + [str(ENTRYPOINT)]


def write_manifest(binary: Path, target: str, commit: str) -> Path:
    manifest = binary.with_name(f"{NAME}.manifest.json")
    manifest.write_text(json.dumps({
        "file": binary.name, "target": target, "sha256": sha256(binary),
        "source_commit": commit, "entrypoint": "scripts/frozen_backend.py",
        "collect_all": list(COLLECT_ALL), "collect_submodules": list(COLLECT_SUBMODULES),
    }, indent=2) + "\n", encoding="utf-8")
    return manifest


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _get(url: str, token: str | None) -> tuple[int, dict]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, {}


def verify_loopback(command: list[str], timeout: float = 120) -> dict:
    """Start the backend in bundled mode and prove it only answers its launcher."""
    port = _free_port()
    token, launch_id = secrets.token_urlsafe(32), secrets.token_hex(16)
    process = subprocess.Popen([*command, "--host", "127.0.0.1", "--port", str(port), "--require-auth"],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    report = {"status": "FAIL", "port": port}
    try:
        assert process.stdin is not None
        process.stdin.write(f"{token}\n{launch_id}\n".encode())
        process.stdin.close()
        url = f"http://127.0.0.1:{port}/health"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                report["error"] = f"backend exited with {process.returncode}"
                return report
            try:
                status, body = _get(url, token)
            except (urllib.error.URLError, OSError):
                time.sleep(1)
                continue
            report["authenticated"] = status
            report["launch_id_echoed"] = body.get("launch_id") == launch_id
            report["unauthenticated"] = _get(url, None)[0]
            if status == 200 and report["launch_id_echoed"] and report["unauthenticated"] == 401:
                report["status"] = "PASS"
            return report
        report["error"] = "backend did not answer before the timeout"
        return report
    finally:
        stop_tree(process)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--target", help="Rust target triple (default: rustc host)")
    parser.add_argument("--work", type=Path, default=ROOT / "build/windows-backend")
    parser.add_argument("--verify", action="store_true", help="run the authenticated loopback check")
    args = parser.parse_args(argv)
    try:
        target = args.target or host_target()
        check_target(target)
        machine = python_machine(args.python)
        if not arch_matches(target, machine):
            raise BuildError(f"{args.python} is {machine}; it cannot build for {target}")
        dist = args.work / "dist"
        subprocess.run(pyinstaller_command(args.python, args.work, dist), cwd=ROOT, check=True)
        built = dist / (f"{NAME}.exe" if "windows" in target else NAME)
        if not built.is_file():
            raise BuildError(f"PyInstaller did not produce {built}")
        with built.open("rb") as handle:
            produced = binary_arch(handle.read(4096))
        if produced != target.split("-", 1)[0]:
            raise BuildError(f"PyInstaller produced a {produced} binary, not {target}")
        BINARIES.mkdir(parents=True, exist_ok=True)
        binary = BINARIES / bundle_name(target)
        shutil.copy2(built, binary)
        manifest = write_manifest(binary, target, source_commit())
        print(json.dumps({"binary": str(binary.relative_to(ROOT)), "manifest": str(manifest.relative_to(ROOT)),
                          "sha256": sha256(binary)}, indent=2))
        if args.verify:
            report = verify_loopback([str(binary)])
            print(json.dumps(report, indent=2))
            return 0 if report["status"] == "PASS" else 1
    except (BuildError, subprocess.CalledProcessError, OSError) as error:
        print(f"build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
