"""Bounded stdin RPC for dedicated Mac runtimes; no arbitrary paths/commands.

Only ephemeral JIT data enters the launcher. stdout contains nonce-bound safe
observations. VM state belongs to a private journal outside eva-readable tools.
"""

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import sys

from macos_tart_host import TartHost
from macos_vm_launcher import Launcher, validate_tools
from macos_vm_recovery import BASE, VMJournal, capacity_lock


KEY = re.compile(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,9}-[1-9][0-9]{0,19}")
FIELDS = {op: {"op", "nonce"} for op in ("inventory", "pending", "ready")}
FIELDS.update({op: {"op", "nonce", "key"} for op in ("state", "names", "proof", "recover", "prepare")})
FIELDS["launch"] = {"op", "nonce", "key", "jit"}


class RuntimeRPC:
    def __init__(self, host, vms, launcher, lock):
        self.host, self.vms, self.launcher, self.lock = host, vms, launcher, Path(lock)
        self.lock_held = False

    def guard(self):
        return nullcontext() if self.lock_held else capacity_lock(self.lock)

    def handle(self, message):
        if (not isinstance(message, dict) or message.get("op") not in FIELDS
                or set(message) != FIELDS[message["op"]]
                or not re.fullmatch(r"[0-9a-f]{32}", str(message.get("nonce")))):
            raise ValueError("Invalid runtime request")
        op = message["op"]
        key = message.get("key")
        if "key" in message and not KEY.fullmatch(str(key)):
            raise ValueError("Exact lease key required")
        h, vms = self.host, self.vms
        if op == "launch":
            value = self.launcher.launch(key, message["jit"])
        elif op == "prepare":
            with self.guard():
                value = vms.prepare(key)
        elif op == "inventory":
            value = h.list_vms()
        elif op == "pending":
            value = vms.pending()
        elif op == "names":
            value = vms.names(key)
        elif op == "state":
            value = vms.state(key)
        elif op == "proof":
            names = vms.names(key)
            if not names:
                raise RuntimeError("Unknown VM lease cannot prove cleanup")
            value = {"processes_absent": all(h.processes_absent(n) is True for n in names),
                     "workspace_absent": all(h.workspace_absent(n) is True for n in names)}
        elif op == "ready":
            with self.guard():
                inventory = h.list_vms()
                value = (not vms.pending() and (h.home / "vms" / BASE).is_dir()
                         and not any(inventory.values())
                         and not any(n.startswith("osvox-probe-") for n in inventory))
        else:
            with self.guard():
                if any(row[2] != key for row in vms.pending()):
                    raise RuntimeError("Different VM lease: quarantine")
                value = vms.reconcile(h.list_vms, h.stop, h.delete, h.instance_identity,
                                      processes_absent=h.processes_absent,
                                      workspace_absent=h.workspace_absent)
        return {"nonce": message["nonce"], "value": value}


def runtime(host_id):
    if host_id == "mini":
        root = Path("/Volumes/AIMATE_WORK/CI/osvox")
        executable = root / "tools/tart.app/Contents/MacOS/tart"
        lock = root / "controller/probe.lock"
        manifest = root / "controller/xcode-26.5-manifest.json"
    elif host_id == "studio":
        root = Path("/Users/osborn/.local/share/osvox-macos-ci")
        executable = root / "tools/tart-2.40.1/tart.app/Contents/MacOS/tart"
        lock = root / "runtime/controller/probe.lock"
        manifest = root / "runtime/controller/xcode-26.5-manifest.json"
    else:
        raise ValueError("Unknown Mac host")
    private = Path("/Users/osborn/.local/state") / ("osvox-macos-runtime-" + host_id)
    for directory in (private.parent, private):
        if directory.is_symlink():
            raise ValueError("Private state cannot use symlinks")
        directory.mkdir(mode=0o700, exist_ok=True)
        st = directory.stat()
        prohibited = 0o022 if directory == private.parent else 0o077
        if directory.resolve() != directory.absolute() or st.st_uid != os.getuid() or st.st_mode & prohibited:
            raise ValueError("Private owned runtime state required")
    host = TartHost(executable, root / "tart")
    validate_tools(host)
    vms = VMJournal(private / "vms.sqlite")
    return RuntimeRPC(host, vms, Launcher(host, vms, lock, manifest), lock)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=("studio", "mini"), required=True)
    parser.add_argument("--session", action="store_true", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    backend = None
    try:
        backend = runtime(args.host)
        with capacity_lock(backend.lock):
            backend.lock_held = True
            backend.launcher.lock_held = True
            while True:
                raw = sys.stdin.buffer.readline(1024 * 1024 + 1)
                if not raw:
                    break
                if len(raw) > 1024 * 1024 or not raw.endswith(b"\n"):
                    raise ValueError("Oversized session request")
                print(json.dumps(backend.handle(json.loads(raw))), flush=True)
    except Exception as error:
        print(json.dumps({"status": "ERROR_FAIL_CLOSED", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    finally:
        if backend is not None:
            backend.vms.close()


if __name__ == "__main__":
    main()
