#!/usr/bin/env python3
"""Build the self-contained engine executable the Windows desktop bundles (T29).

PyInstaller freezes ``scripts/frozen_engine.py`` with exactly the parameters
the macOS release uses (scripts/build_voxagent_dmg.sh), so both packages run
the same entrypoint. The result is written as
``apps/desktop-tauri/src-tauri/binaries/voxagent-engine-<target-triple>[.exe]``
for Tauri ``externalBin``; ``tauri build --config
src-tauri/tauri.release-engine.conf.json`` then installs it beside
``Vox Agent.exe``, the only place a release build looks for it (T29a).

PyInstaller cannot cross-compile: build on the target OS (a Windows runner
for Windows). Needs ``pip install . pyinstaller`` in the chosen Python.

    python scripts/build_windows_engine.py                 # host target
    python scripts/build_windows_engine.py --verify        # also run the loopback check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARIES = ROOT / "apps/desktop-tauri/src-tauri/binaries"
ENTRYPOINT = ROOT / "scripts/frozen_engine.py"
# Must stay identical to build_voxagent_dmg.sh (checked by the tests).
COLLECT_ALL = ("fastapi", "uvicorn", "websockets", "aiosqlite", "httpx",
               "structlog", "dotenv", "pydantic", "multipart")
_TRIPLE = re.compile(r"^[a-z0-9_]+-[a-z0-9_]+-[a-z0-9_]+(-[a-z0-9_]+)?$")


class BuildError(RuntimeError):
    pass


def bundle_name(target: str) -> str:
    return f"voxagent-engine-{target}" + (".exe" if "windows" in target else "")


def check_target(target: str, host_system: str | None = None) -> None:
    if not _TRIPLE.match(target):
        raise BuildError(f"not a target triple: {target!r}")
    system = host_system or platform.system()
    wants = "Windows" if "windows" in target else "Darwin" if "apple" in target else "Linux"
    if wants != system:
        raise BuildError(f"PyInstaller cannot cross-compile: target {target} needs a {wants} host, not {system}")


_ARCH_ALIASES = {"x86_64": {"x86_64", "amd64"}, "aarch64": {"aarch64", "arm64"}}
_MACHO = {0x01000007: "x86_64", 0x0100000C: "aarch64"}
_PE = {0x8664: "x86_64", 0xAA64: "aarch64"}


def arch_matches(target: str, machine: str) -> bool:
    """Whether a Python reporting `platform.machine()` builds for `target`."""
    return machine.lower() in _ARCH_ALIASES.get(target.split("-", 1)[0], set())


def binary_arch(data: bytes) -> str | None:
    """CPU architecture of a PE or 64-bit Mach-O image, else None."""
    from verify_windows_release_preflight import _pe_machine

    if data[:2] == b"MZ":
        offset = int.from_bytes(data[0x3C:0x40], "little")
        if _pe_machine(data) is None:
            return None
        return _PE.get(int.from_bytes(data[offset + 4:offset + 6], "little"))
    if data[:4] == (0xFEEDFACF).to_bytes(4, "little"):
        return _MACHO.get(int.from_bytes(data[4:8], "little"))
    return None


def python_machine(python: str) -> str:
    return subprocess.run([python, "-c", "import platform; print(platform.machine())"],
                          capture_output=True, text=True, check=True).stdout.strip()


def host_target() -> str:
    output = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    for line in output.splitlines():
        if line.startswith("host: "):
            return line.split(": ", 1)[1].strip()
    raise BuildError("rustc did not report a host target")


def pyinstaller_command(python: str, work: Path, dist: Path) -> list[str]:
    command = [python, "-m", "PyInstaller", "--clean", "--noconfirm", "--onefile",
               "--distpath", str(dist), "--workpath", str(work / "work"),
               "--specpath", str(work / "spec"), "--paths", str(ROOT),
               "--name", "voxagent-engine"]
    for package in COLLECT_ALL:
        command += ["--collect-all", package]
    return command + [str(ENTRYPOINT)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(binary: Path, target: str, source_commit: str) -> Path:
    manifest = binary.with_name("voxagent-engine.manifest.json")
    manifest.write_text(json.dumps({
        "file": binary.name, "target": target, "sha256": sha256(binary),
        "source_commit": source_commit, "entrypoint": "scripts/frozen_engine.py",
        "collect_all": list(COLLECT_ALL),
    }, indent=2) + "\n", encoding="utf-8")
    return manifest


def source_commit() -> str:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    if head.returncode != 0 or dirty.returncode != 0:
        return "unknown"
    return head.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--target", help="Rust target triple (default: rustc host)")
    parser.add_argument("--work", type=Path, default=ROOT / "build/windows-engine")
    parser.add_argument("--verify", action="store_true", help="run the loopback check on the result")
    args = parser.parse_args(argv)
    try:
        target = args.target or host_target()
        check_target(target)
        machine = python_machine(args.python)
        if not arch_matches(target, machine):
            raise BuildError(f"{args.python} is {machine}; it cannot build for {target}")
        dist = args.work / "dist"
        subprocess.run(pyinstaller_command(args.python, args.work, dist), cwd=ROOT, check=True)
        built = dist / ("voxagent-engine.exe" if "windows" in target else "voxagent-engine")
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
            import verify_engine_loopback
            report = verify_engine_loopback.run([str(binary)], timeout=120)
            print(json.dumps(report, indent=2))
            return 0 if report["status"] == "PASS" else 1
    except (BuildError, subprocess.CalledProcessError, OSError) as error:
        print(f"build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
