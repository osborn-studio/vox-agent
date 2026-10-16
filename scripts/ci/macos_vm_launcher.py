"""Blocking one-job launcher for a dedicated local Mac runtime.

Caller records GitHub registration/launch intent first. This launcher records VM
intent before clone; all exceptions preserve ownership for independent recovery.
No GitHub credentials, host shares, personal directories or workflow cancellation.
"""

import hashlib
from contextlib import nullcontext
import os
from pathlib import Path
import stat
import subprocess
import time

from macos_image_verify import VERIFY_TIMEOUT, verify_clone as verify
from macos_vm_recovery import BASE, capacity_lock
from macos_vm_recovery import VMJournal as VMJournal


TART_SHA256 = "20c85c3c9d5daba5fd0e409d23e1203a42bfccf4f5c4dafb5bb7ffbfe33e82b5"
SOFTNET_SHA256 = "e5e35d61a6dd7e35a3134732e766c897b7cb6f097080ff00e1e75adeab0471c7"
CLONE_TIMEOUT = 180
BOOT_TIMEOUT = 180
RUNNER_TIMEOUT = 1500
LAUNCH_TIMEOUT = CLONE_TIMEOUT + VERIFY_TIMEOUT + BOOT_TIMEOUT + RUNNER_TIMEOUT + 6 * 30 + 180
BLOCK = ",".join("out " + v for v in ("@host", "10.0.0.0/8", "172.16.0.0/12",
                                     "192.168.0.0/16", "100.64.0.0/10",
                                     "169.254.0.0/16", "127.0.0.0/8",
                                     "224.0.0.0/4", "240.0.0.0/4"))
PREP = """
import pathlib, subprocess
services=subprocess.check_output(['/usr/sbin/networksetup','-listallnetworkservices'],text=True).splitlines()[1:]
for service in services:
    if service and not service.startswith('*'):
        subprocess.run(['/usr/bin/sudo','-n','/usr/sbin/networksetup','-setdnsservers',service,'1.1.1.1','8.8.8.8'],check=True)
r=pathlib.Path('/Users/admin/actions-runner')
assert r.is_dir()
print(subprocess.check_output([str(r/'bin/Runner.Listener'),'--version'],text=True).strip())
"""
RUN = """
import os, subprocess, sys
config=sys.stdin.read().strip()
os.chdir('/Users/admin/actions-runner')
# No check=True exception: it would contain the ephemeral guest argv.
raise SystemExit(subprocess.call(['./run.sh','--jitconfig',config]))
"""


def validate_tools(host):
    if host.home.resolve() != host.home.absolute():
        raise ValueError("Canonical dedicated Tart home required")
    st = host.home.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise ValueError("Untrusted Tart home")
    executable = host.executable
    if (executable.resolve() != executable.absolute() or executable.stat().st_uid != os.getuid()
            or executable.stat().st_mode & 0o022
            or hashlib.sha256(executable.read_bytes()).hexdigest() != TART_SHA256):
        raise ValueError("Untrusted Tart executable")
    helper = Path("/Library/OSVOX-CI/bin/softnet")
    st = helper.stat()
    if (helper.resolve() != helper.absolute() or st.st_uid != 0 or st.st_mode & 0o022
            or not stat.S_ISREG(st.st_mode) or not st.st_mode & stat.S_ISUID
            or hashlib.sha256(helper.read_bytes()).hexdigest() != SOFTNET_SHA256):
        raise ValueError("Untrusted privileged network helper")
    for directory in (helper.parent, helper.parent.parent):
        st = directory.stat()
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError("Untrusted privileged helper directory")
    host._call(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(executable.parents[2])])


class Launcher:
    def __init__(self, host, vms, lock, manifest, *, popen=subprocess.Popen):
        self.host, self.vms = host, vms
        self.lock, self.manifest, self.popen = Path(lock), Path(manifest), popen
        self.lock_held = False

    def launch(self, key, jit):
        if not isinstance(jit, str) or not 1 <= len(jit) <= 65536:
            raise ValueError("Invalid JIT configuration")
        h = self.host
        with nullcontext() if self.lock_held else capacity_lock(self.lock):
            validate_tools(h)
            if self.vms.state(key) is None:
                name = self.vms.prepare(key)
            else:
                pending = self.vms.pending()
                if len(pending) != 1 or pending[0][2] != key or pending[0][3] != "prepared":
                    raise RuntimeError("Prepared VM cannot be replayed")
                name = pending[0][0]
            self.vms.start_clone(name)
            h._call([str(h.executable), "clone", BASE, name], timeout=CLONE_TIMEOUT)
            self.vms.bind_instance(name, h.instance_identity(name))
            verification = verify(self.manifest, h.home / "vms" / name)
            if verification.get("status") != "PINNED_IMAGE_BYTES_VERIFIED":
                raise ValueError("Clone byte verification incomplete")
            h._call([str(h.executable), "set", name, "--cpu", "4", "--memory", "6144"])
            environment = {"PATH": "/Library/OSVOX-CI/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                           "TART_HOME": str(h.home), "TART_NO_AUTO_PRUNE": "1"}
            boot = self.popen(
                [str(h.executable), "run", name, "--no-graphics", "--no-audio", "--no-clipboard",
                 "--no-usb-accessories", "--net-softnet-block", BLOCK],
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            deadline = time.monotonic() + BOOT_TIMEOUT
            while time.monotonic() < deadline:
                try:
                    h._call([str(h.executable), "exec", name, "/usr/bin/true"], timeout=10)
                    break
                except RuntimeError:
                    if boot.poll() is not None:
                        raise RuntimeError("Disposable guest exited before readiness") from None
                    time.sleep(2)
            else:
                raise RuntimeError("Disposable guest readiness timeout")
            version = h._call([str(h.executable), "exec", name, "/usr/bin/python3", "-c", PREP]).strip()
            if version != "2.337.0":
                raise ValueError("Guest runner version changed: requalification required")
            client = self.popen(
                [str(h.executable), "exec", "-i", name, "/usr/bin/python3", "-c", RUN],
                env=environment, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                client.communicate(input=jit.encode(), timeout=RUNNER_TIMEOUT)
            except subprocess.TimeoutExpired:
                client.kill()
                client.wait(timeout=15)
                raise RuntimeError("Guest runner timeout: lease remains quarantined") from None
            if client.returncode:
                raise RuntimeError("Guest runner failed: preserve lease")
            version_after = h._call([str(h.executable), "exec", name,
                                     "/Users/admin/actions-runner/bin/Runner.Listener", "--version"]).strip()
            if version_after != "2.337.0":
                raise ValueError("Guest runner changed during job: requalification required")
            # Blocking contract: normal return means the ephemeral runner exited.
            # Recovery independently verifies terminal assignment and cleans VM.
            return {"vm": name, "runner_exited": True, "byte_verification": verification}
