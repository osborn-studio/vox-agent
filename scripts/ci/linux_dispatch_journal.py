"""Durable, single-slot lease journal. Unknown outcomes quarantine the slot.

This is controller state only. It neither starts nor kills a VM and never retries
an interrupted lease. Production reconciliation must obtain independent evidence
from the GitHub API AND the dedicated remote runtime before calling resolve().
"""

import os
from pathlib import Path
import re
import sqlite3


class LeaseJournal:
    def __init__(self, path):
        path = Path(path)
        parent = path.parent
        st = parent.stat()
        if parent.resolve() != parent.absolute() or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError("Journal needs a private owned directory")
        if path.is_symlink():
            raise ValueError("Journal cannot be a symlink")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            st = path.stat()
            if st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise ValueError("Journal must be private and owned")
        else:
            os.close(fd)
        self.db = sqlite3.connect(path, timeout=3, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS leases (
            lease_key TEXT PRIMARY KEY, state TEXT NOT NULL,
            runner_id INTEGER, runner_name TEXT NOT NULL UNIQUE)""")

    def close(self):
        self.db.close()

    def reserve(self, key, runner_name):
        if not re.fullmatch(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,9}-[1-9][0-9]{0,19}", key):
            raise ValueError("Invalid run-attempt-job key")
        if not re.fullmatch(r"(?:osvox|wiki)-prod-[0-9a-f]{32}", runner_name):
            raise ValueError("Invalid owned runner name")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT 1 FROM leases WHERE state != 'resolved' LIMIT 1").fetchone():
                raise RuntimeError("Unresolved lease: reconcile before scheduling")
            # Persist registration intent BEFORE the external API is called.
            self.db.execute(
                "INSERT INTO leases VALUES (?, ?, NULL, ?)",
                (key, "registration_unknown", runner_name),
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def registered(self, key, runner_id):
        if type(runner_id) is not int or runner_id < 1:
            raise ValueError("Invalid runner id")
        result = self.db.execute(
            "UPDATE leases SET runner_id=?, state='registered_not_started' WHERE lease_key=? AND state='registration_unknown'",
            (runner_id, key),
        )
        if result.rowcount != 1:
            raise RuntimeError("Invalid journal transition")

    def launch_intent(self, key):
        # Commit before handing JIT credentials to SSH. A crash here is uncertain.
        result = self.db.execute(
            "UPDATE leases SET state='lease_unknown' WHERE lease_key=? AND state='registered_not_started'",
            (key,),
        )
        if result.rowcount != 1:
            raise RuntimeError("Invalid launch transition")

    def pending(self):
        return self.db.execute(
            "SELECT lease_key,state,runner_id,runner_name FROM leases WHERE state != 'resolved'"
        ).fetchall()

    def resolve(self, key, proof):
        row = self.db.execute(
            "SELECT runner_id,runner_name,state FROM leases WHERE lease_key=?", (key,)
        ).fetchone()
        if not row or row[2] == "resolved":
            raise RuntimeError("No unresolved lease")
        required = (
            "job_terminal",
            "runner_absent",
            "remote_processes_absent",
            "owned_workspace_absent",
        )
        if (
            proof.get("lease_key") != key
            or proof.get("runner_id") != row[0]
            or proof.get("runner_name") != row[1]
            or any(proof.get(flag) is not True for flag in required)
        ):
            raise ValueError("Incomplete reconciliation: keep slot quarantined")
        self.db.execute("UPDATE leases SET state='resolved' WHERE lease_key=?", (key,))
