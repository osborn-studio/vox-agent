"""Single-slot fleet scheduling core, not a deployed launcher or launchd service.

One central journal prevents Studio/mini racing to register the same job. The
caller holds the fleet lock, while each host runtime holds its own capacity lock.
Adapters must freshly approve the exact workflow/job/group and independently
recover the bound host. No credentials are written to the placement journal.
"""

import uuid

from linux_dispatch_journal import LeaseJournal as LeaseJournal


HOSTS = ("studio", "mini")
KIND = "osvox-mac-fleet-v1"


def initialize(journal):
    db = journal.db
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("CREATE TABLE IF NOT EXISTS fleet_identity (kind TEXT PRIMARY KEY)")
        row = db.execute("SELECT kind FROM fleet_identity").fetchall()
        if not row:
            if db.execute("SELECT 1 FROM leases LIMIT 1").fetchone():
                raise ValueError("Cannot adopt another controller's lease journal")
            db.execute("INSERT INTO fleet_identity VALUES (?)", (KIND,))
        elif row != [(KIND,)]:
            raise ValueError("Wrong fleet journal kind")
        db.execute("CREATE TABLE IF NOT EXISTS placements (lease_key TEXT PRIMARY KEY, host TEXT NOT NULL)")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def bind(journal, key, host):
    if host not in HOSTS or key not in {p[0] for p in journal.pending()}:
        raise ValueError("Exact pending lease and trusted host required")
    journal.db.execute("INSERT INTO placements VALUES (?,?)", (key, host))


def placement(journal, key):
    row = journal.db.execute("SELECT host FROM placements WHERE lease_key=?", (key,)).fetchone()
    if not row or row[0] not in HOSTS:
        raise RuntimeError("Unknown lease host: quarantine, never fallback")
    return row[0]


def cycle(config, journal, hosts, *, discover=None, approve=None, register=None):
    if journal.db.execute("SELECT kind FROM fleet_identity").fetchall() != [(KIND,)]:
        raise ValueError("Wrong fleet journal kind")
    pending = journal.pending()
    if len(pending) > 1:
        raise ValueError("Multiple pending fleet leases")
    if pending:
        key = pending[0][0]
        host = placement(journal, key)
        hosts[host].recover(key)
        if journal.pending():
            return "QUARANTINED"
    # Recovery runs even if host/admission qualification config is missing.
    if config.get("routing_approved") is not True:
        return "DISABLED"
    qualified = config.get("qualified_hosts")
    if (not isinstance(qualified, list) or not qualified
            or any(type(h) is not str or h not in HOSTS for h in qualified)
            or len(set(qualified)) != len(qualified)):
        raise ValueError("Explicit qualified fleet hosts required")
    if any(callback is None for callback in (discover, approve, register)):
        raise RuntimeError("Qualified scheduling adapters required")
    selected = None
    for host in HOSTS:
        if host in qualified and hosts[host].ready() is True:
            selected = host
            break
    if selected is None:
        return "NO_QUALIFIED_CAPACITY"
    for candidate in discover():
        if approve(candidate) is not True:
            continue
        key = candidate["key"]
        name = "osvox-prod-" + uuid.uuid4().hex
        journal.reserve(key, name)
        # A crash in this gap quarantines an unplaced registration intent.
        # No external side effect has occurred, and no automatic replay exists.
        bind(journal, key, selected)
        # Record the host VM name before registration as well: a lost API reply
        # can then be reconciled against an actual, independent VM intent.
        hosts[selected].prepare(key)
        jit = register(name, candidate["labels"])
        if (not isinstance(jit, dict) or not isinstance(jit.get("runner"), dict)
                or jit["runner"].get("name") != name
                or not isinstance(jit.get("encoded_jit_config"), str)
                or not 1 <= len(jit["encoded_jit_config"]) <= 65536):
            raise ValueError("Uncertain registration response: quarantine")
        journal.registered(key, jit["runner"]["id"])
        journal.launch_intent(key)
        hosts[selected].launch(key, jit["encoded_jit_config"])
        del jit
        return "LEASE_STARTED"
    return "IDLE"
