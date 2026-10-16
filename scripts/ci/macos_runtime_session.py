"""Nonce-bound stdin/stdout transport holding a runtime's host capacity lock.

Commands come only from trusted installation profiles. JIT data is never argv,
logged, written to disk, or retained in an exception message.
"""

import json
import os
from pathlib import Path
import select
import subprocess
import time
import uuid


class Session:
    def __init__(self, command):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, start_new_session=True,
                                        env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                                             "LANG": "en_US.UTF-8"})
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.buffer = b""
        self.launch_in_progress = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.process.stdin.close()
        if self.launch_in_progress and self.process.poll() is None:
            # The runtime keeps its host lock until its bounded launcher exits.
            # Quarantine must not kill a healthy guest simply to close a pipe.
            self.process.stdout.close()
            return
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process.stdout.close()

    def call(self, op, *, key=None, jit=None, timeout=60):
        if op == "launch":
            self.launch_in_progress = True
        nonce = uuid.uuid4().hex
        message = {"op": op, "nonce": nonce}
        if key is not None:
            message["key"] = key
        if jit is not None:
            message["jit"] = jit
        data = json.dumps(message).encode() + b"\n"
        if len(data) > 1024 * 1024:
            raise ValueError("Oversized runtime request")
        deadline = time.monotonic() + timeout
        try:
            offset = 0
            while offset < len(data):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [self.process.stdin], [], remaining)[1]:
                    raise RuntimeError("Runtime request timeout")
                offset += os.write(self.process.stdin.fileno(), data[offset:])
            del data, message
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                    raise RuntimeError("Runtime response timeout")
                block = os.read(self.process.stdout.fileno(), 65536)
                if not block:
                    raise RuntimeError("Runtime session closed unexpectedly")
                self.buffer += block
                if len(self.buffer) > 1024 * 1024:
                    raise RuntimeError("Oversized runtime response")
            line, self.buffer = self.buffer.split(b"\n", 1)
            reply = json.loads(line)
        except (OSError, ValueError):
            raise RuntimeError("Runtime transport/response invalid") from None
        if not isinstance(reply, dict) or set(reply) != {"nonce", "value"} or reply["nonce"] != nonce:
            raise RuntimeError("Runtime observation unconfirmed")
        if op == "launch":
            self.launch_in_progress = False
        return reply["value"]


class RemoteVMJournal:
    def __init__(self, session, key):
        self.session, self.key = session, key

    def pending(self):
        value = self.session.call("pending")
        if not isinstance(value, list):
            raise ValueError("Invalid remote ownership inventory")
        return value

    def state(self, key):
        return self.session.call("state", key=key)

    def names(self, key):
        value = self.session.call("names", key=key)
        if not isinstance(value, list) or any(type(n) is not str for n in value):
            raise ValueError("Invalid remote VM names")
        return value

    def reconcile(self, *callbacks, **options):
        # The pinned runtime performs its own trusted checks while its lock stays
        # held for subsequent GitHub removal and all final independent proofs.
        # Includes stop wait, multiple full inventories, process checks and
        # deletion; longer than their combined bounds plus SSH startup margin.
        return self.session.call("recover", key=self.key, timeout=600)
