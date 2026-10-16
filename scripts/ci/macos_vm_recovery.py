"""Durable macOS VM ownership, independent of GitHub admission.

Hold the shared Mac capacity lock across reserve, clone, run and cleanup. This
module only reconciles VM resources; it NEVER resolves a GitHub runner lease.
A caller must independently reconcile the exact job and runner before reuse.
"""

import fcntl
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid
from contextlib import contextmanager

BASE = "osvox-xcode-26-5-923c98d"
BASE_DIGEST = "923c98d32e40ffadb6e6815a9722124b7a57bdf7d7763a708a2b28d1970831bd"
NAME = re.compile(r"osvox-probe-[0-9a-f]{32}")


def private_directory(path):
    path = Path(path)
    st = path.stat()
    if path.resolve() != path.absolute() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError("Private owned directory required")
    return path


@contextmanager
def capacity_lock(path):
    """Use the same file as the prior Mac qualification controller."""
    path = Path(path)
    parent = path.parent
    st = parent.stat()
    if parent.resolve() != parent.absolute() or st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise ValueError("Untrusted lock directory")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise ValueError("Untrusted lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def checked_inventory(list_vms):
    inventory = list_vms()
    if not isinstance(inventory, dict) or any(
        type(k) is not str or type(v) is not bool for k, v in inventory.items()
    ):
        raise ValueError("Untrusted inventory")
    return inventory


class VMJournal:
    def __init__(self, path):
        path = Path(path)
        private_directory(path.parent)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise ValueError("Untrusted VM journal")
        finally:
            os.close(fd)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=3)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS owned_vms (name TEXT PRIMARY KEY, base TEXT NOT NULL, lease_key TEXT UNIQUE NOT NULL, state TEXT NOT NULL, instance TEXT)"
        )

    def close(self):
        self.db.close()

    def reserve(self, lease_key):
        if not re.fullmatch(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,9}-[1-9][0-9]{0,19}", lease_key):
            raise ValueError("Exact run-attempt-job key required")
        name = "osvox-probe-" + uuid.uuid4().hex
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.pending():
                raise RuntimeError("Unreconciled VM blocks new work")
            self.db.execute(
                "INSERT INTO owned_vms VALUES (?,?,?,?,NULL)",
                (name, BASE_DIGEST, lease_key, "clone_unknown"),
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return name

    def bind_instance(self, name, identity):
        if not re.fullmatch(r"[0-9]+:[0-9]+", identity):
            raise ValueError("Expected device:inode identity")
        row = self.db.execute(
            "UPDATE owned_vms SET instance=?,state='owned' WHERE name=? AND state='clone_unknown'",
            (identity, name),
        )
        if row.rowcount != 1:
            raise RuntimeError("Invalid ownership transition")

    def prepare(self, key):
        name = self.reserve(key)
        self.db.execute("UPDATE owned_vms SET state='prepared' WHERE name=? AND state='clone_unknown'", (name,))
        return name

    def start_clone(self, name):
        row = self.db.execute(
            "UPDATE owned_vms SET state='clone_unknown' WHERE name=? AND state='prepared'", (name,)
        )
        if row.rowcount != 1:
            raise RuntimeError("Clone intent cannot be replayed")

    def pending(self):
        return self.db.execute(
            "SELECT name,base,lease_key,state,instance FROM owned_vms WHERE state!='vm_absent'"
        ).fetchall()

    def state(self, key):
        row = self.db.execute("SELECT state FROM owned_vms WHERE lease_key=?", (key,)).fetchone()
        return row[0] if row else None

    def names(self, key):
        return [row[0] for row in self.db.execute(
            "SELECT name FROM owned_vms WHERE lease_key=?", (key,)
        ).fetchall()]

    def reconcile(self, list_vms, stop, delete, instance_identity, *, processes_absent=None,
                  workspace_absent=None):
        """Callbacks are trusted host adapters, never supplied by repository jobs.

        list_vms must return the full local VM inventory, mapping name to running
        bool. API/command uncertainty leaves ownership pending, never retrying a
        clone or touching unrecorded VMs. Stop must wait for the exact VM to stop.
        """
        results = []
        for name, base, key, state, identity in self.pending():
            if (
                not NAME.fullmatch(name)
                or base != BASE_DIGEST
                or state not in ("prepared", "clone_unknown", "owned")
                or (state == "owned" and not re.fullmatch(r"[0-9]+:[0-9]+", str(identity)))
            ):
                raise ValueError("Invalid persisted ownership")
            inventory = checked_inventory(list_vms)
            if name in inventory:
                if state != "owned" or identity != instance_identity(name):
                    raise RuntimeError("Unbound or replaced VM: quarantine")
                if inventory[name]:
                    stop(name)
                inventory = checked_inventory(list_vms)
                if inventory.get(name) is True:
                    raise RuntimeError("Owned VM remains running")
                if processes_absent is None or processes_absent(name) is not True:
                    raise RuntimeError("Owned VM process absence unconfirmed")
                if name in inventory:
                    if identity != instance_identity(name):
                        raise RuntimeError("VM replaced during cleanup")
                    delete(name)
            if name in checked_inventory(list_vms):
                raise RuntimeError("Owned VM still exists")
            # A clone child can outlive its controller and finish after an empty
            # inventory. Do not release durable ownership until it has exited.
            if processes_absent is None or processes_absent(name) is not True:
                raise RuntimeError("Owned VM process absence unconfirmed")
            if workspace_absent is None or workspace_absent(name) is not True:
                raise RuntimeError("Owned VM workspace absence unconfirmed")
            self.db.execute("UPDATE owned_vms SET state='vm_absent' WHERE name=?", (name,))
            results.append(
                {"name": name, "lease_key": key, "vm_absent": True, "github_lease_resolved": False}
            )
        return results
