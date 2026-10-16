"""Trusted local Tart recovery adapter, usable with either Mac's dedicated paths.

No registration, launch, shell, bulk cleanup, process kill or host shares. The
coordinator holds the host's existing capacity lock for the entire operation.
"""

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time


NAME = re.compile(r"osvox-probe-[0-9a-f]{32}")


class TartHost:
    def __init__(self, executable, home, *, run=subprocess.run):
        self.executable = Path(executable)
        self.home = Path(home)
        if not self.executable.is_absolute() or not self.home.is_absolute():
            raise ValueError("Absolute trusted Tart paths required")
        self.run = run

    def _call(self, command, timeout=30):
        try:
            result = self.run(
                command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                env={"PATH": "/Library/OSVOX-CI/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                     "TART_HOME": str(self.home), "TART_NO_AUTO_PRUNE": "1",
                     "LANG": "en_US.UTF-8"},
            )
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            raise RuntimeError("Tart host observation unavailable") from None
        if result.returncode:
            raise RuntimeError("Tart host command failed")
        return result.stdout

    def list_vms(self):
        try:
            rows = json.loads(self._call([str(self.executable), "list", "--format", "json"]))
        except ValueError:
            raise ValueError("Invalid Tart inventory") from None
        if not isinstance(rows, list):
            raise ValueError("Invalid Tart inventory")
        result = {}
        for row in rows:
            if (not isinstance(row, dict) or row.get("Source") not in ("local", "OCI")
                    or type(row.get("Name")) is not str or not row["Name"]
                    or type(row.get("Running")) is not bool):
                raise ValueError("Invalid Tart inventory record")
            if row["Source"] == "local":
                if row["Name"] in result:
                    raise ValueError("Duplicate local VM inventory record")
                result[row["Name"]] = row["Running"]
        return result

    def _name(self, name):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("Exact owned VM name required")
        return name

    def instance_identity(self, name):
        directory = self.home / "vms" / self._name(name)
        if directory.resolve() != directory.absolute():
            raise ValueError("VM identity cannot follow symlinks")
        st = directory.lstat()
        if not stat.S_ISDIR(st.st_mode):
            raise ValueError("VM directory required")
        return f"{st.st_dev}:{st.st_ino}"

    def processes_absent(self, name):
        name = self._name(name)
        # Both tart clone and tart run must be absent. Fail closed on any process
        # mentioning this randomized VM name, including wrapper/child commands.
        output = self._call(["/bin/ps", "-axww", "-o", "uid=,pid=,ucomm=,args="])
        if len(output) > 16 * 1024 * 1024:
            raise RuntimeError("Process inventory too large")
        saw_record = False
        for row in output.splitlines():
            parts = row.split(None, 3)
            if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
                # BSD ps can emit literal newlines inside argv. Continuations
                # still participate in the full randomized-name scan below.
                if not saw_record:
                    raise RuntimeError("Untrusted process inventory")
                continue
            saw_record = True
            # Softnet's arguments identify a MAC/FD, not a VM name. This runtime
            # has one slot: any helper still alive blocks reuse, even if root
            # hides its arguments. Never kill another user's helper.
            if parts[2] in ("softnet", "softnet-0.24.1"):
                return False
            if parts[2] == "tart" and (int(parts[0]) != os.getuid() or len(parts) != 4
                                      or parts[3] in ("tart", "(tart)")):
                raise RuntimeError("Tart process arguments/owner unobservable")
        return not any(name in row for row in output.splitlines())

    def workspace_absent(self, name):
        directory = self.home / "vms" / self._name(name)
        try:
            directory.lstat()
        except FileNotFoundError:
            return True
        return False

    def stop(self, name, *, timeout=30):
        name = self._name(name)
        self._call([str(self.executable), "stop", name, "--timeout", "15"], timeout=30)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.list_vms().get(name) is not True and self.processes_absent(name):
                return
            time.sleep(0.2)
        raise RuntimeError("Owned VM stop not confirmed")

    def delete(self, name):
        name = self._name(name)
        if self.list_vms().get(name) is True or not self.processes_absent(name):
            raise RuntimeError("Owned VM still running")
        self._call([str(self.executable), "delete", name])
