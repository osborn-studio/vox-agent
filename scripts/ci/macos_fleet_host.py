"""Fixed-host fleet adapters. A session retains the host lock through API recovery."""

from pathlib import Path
import shlex

from macos_dispatch_reconcile import reconcile_pending
from macos_runtime_session import RemoteVMJournal, Session
from macos_vm_launcher import LAUNCH_TIMEOUT


class Host:
    def __init__(self, name, journal, api, source):
        if name not in ("studio", "mini") or not Path(source).is_absolute():
            raise ValueError("Fixed host and installed absolute source required")
        self.name, self.journal, self.api = name, journal, api
        script = str(Path(source) / "macos_runtime_rpc.py")
        if name == "mini":
            self.command = ["/usr/bin/python3", "-E", "-s", script, "--host", name, "--session"]
        else:
            self.command = ["/usr/bin/ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15",
                            "-o", "UserKnownHostsFile=/Users/osborn/.ssh/known_hosts",
                            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ServerAliveInterval=30",
                            "-o", "ServerAliveCountMax=3",
                            "-i", "/Users/osborn/.ssh/id_ed25519_osvox_studio_ci",
                            "osborn@100.88.201.123",
                            "/usr/bin/python3 -E -s " + shlex.quote(script) + " --host studio --session"]
        self.session = None
        self.error = None

    def connect(self):
        if self.session is None:
            self.session = Session(self.command)
        return self.session

    def close(self):
        if self.session is not None:
            self.session.close()
            self.session = None

    def ready(self):
        try:
            value = self.connect().call("ready")
            if value is not True:
                self.close()
            return value is True
        except (OSError, RuntimeError, ValueError):
            self.error = "HOST_UNAVAILABLE_OR_UNQUALIFIED"
            self.close()
            return False

    def launch(self, key, jit):
        # Blocking: normal return is after the one-job guest runner has exited.
        return self.connect().call("launch", key=key, jit=jit, timeout=LAUNCH_TIMEOUT)

    def prepare(self, key):
        return self.connect().call("prepare", key=key)

    def recover(self, key):
        session = self.connect()
        vms = RemoteVMJournal(session, key)

        def proof():
            value = session.call("proof", key=key)
            if not isinstance(value, dict) or set(value) != {"processes_absent", "workspace_absent"}:
                raise ValueError("Unconfirmed remote cleanup proof")
            return value

        def unused(*args):
            raise RuntimeError("Remote runtime owns these VM operations")

        return reconcile_pending(
            self.journal, vms, self.api, list_vms=lambda: session.call("inventory"),
            stop=unused, delete=unused, instance_identity=unused,
            owned_processes_absent=lambda k: proof()["processes_absent"],
            owned_workspace_absent=lambda k: proof()["workspace_absent"],
            vm_processes_absent=unused, vm_workspace_absent=unused,
        )
