"""Mac recovery coordinator; caller holds the shared capacity lock throughout.

Run before admission, including when routing is disabled or config is invalid.
Adapters are trusted controller code. api returns None ONLY for an independently
observed HTTP 404; transport/auth errors must raise. No run cancellation exists.
This module is not a launcher or a persistent service.
"""

import re

ORG = "orgs/osborn-studio/actions"
REPO = "repos/osborn-studio/osvox/actions"
GROUP = 3


def inventory(api):
    result = []
    for page in range(1, 11):
        body = api(f"{ORG}/runner-groups/{GROUP}/runners?per_page=100&page={page}")
        if not isinstance(body, dict) or not isinstance(body.get("runners"), list):
            raise ValueError("Invalid runner inventory")
        rows = body["runners"]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("Invalid runner record")
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise RuntimeError("Incomplete runner inventory; quarantine")


def reconcile_pending(leases, vms, api, *, list_vms, stop, delete, instance_identity,
                      owned_processes_absent, owned_workspace_absent, vm_processes_absent,
                      vm_workspace_absent):
    """Stop owned guests first; clear leases only on independent terminal proof.

    A lost registration response with no matching runner stays quarantined:
    inventory absence cannot prove the registration request won't finish later.
    Such a case needs operator reconciliation, never registration replay.
    """
    pending = leases.pending()
    if len(pending) > 1:
        raise ValueError("Multiple pending Mac leases")
    for key, state, runner_id, name in pending:
        if (not re.fullmatch(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,9}-[1-9][0-9]{0,19}", key)
                or not re.fullmatch(r"osvox-prod-[0-9a-f]{32}", name)
                or state not in ("registration_unknown", "registered_not_started", "lease_unknown")
                or (runner_id is not None and (type(runner_id) is not int or runner_id < 1))
                or (state != "registration_unknown" and runner_id is None)):
            raise ValueError("Invalid persisted Mac lease")
    if any(row[2] not in {p[0] for p in pending} for row in vms.pending()):
        raise ValueError("VM without matching GitHub lease; quarantine")
    vms.reconcile(list_vms, stop, delete, instance_identity, processes_absent=vm_processes_absent,
                  workspace_absent=vm_workspace_absent)
    resolved = []
    for key, state, runner_id, name in pending:
        run_id, attempt, job_id = map(int, key.split("-"))
        job = api(f"{REPO}/jobs/{job_id}")
        if not isinstance(job, dict) or any(
            type(job.get(field)) is not int or job[field] != expected
            for field, expected in (("id", job_id), ("run_id", run_id), ("run_attempt", attempt))
        ) or job.get("name") != "swift-build":
            raise ValueError("Exact Swift job identity required")
        if job.get("status") != "completed":
            continue
        matches = [r for r in inventory(api) if r.get("name") == name or r.get("id") == runner_id]
        if len(matches) > 1:
            raise ValueError("Ambiguous runner ownership")
        if matches:
            runner = matches[0]
            if (runner.get("name") != name or type(runner.get("id")) is not int
                    or runner["id"] < 1 or runner.get("busy") is not False
                    or (runner_id is not None and runner_id != runner["id"])):
                raise ValueError("Runner ownership/state mismatch")
            if runner_id is None:
                runner_id = runner["id"]
                leases.registered(key, runner_id)
        elif runner_id is None:
            raise RuntimeError("Uncertain registration outcome; operator reconciliation required")
        assigned = (job.get("runner_id"), job.get("runner_name"))
        if state == "lease_unknown":
            if type(assigned[0]) is not int or assigned != (runner_id, name):
                raise ValueError("Launched job assignment mismatch")
            if vms.state(key) != "vm_absent":
                raise ValueError("Launched lease lacks durable VM absence")
        elif (assigned[0] is not None and (type(assigned[0]) is not int or assigned[0] != 0)) or assigned[1] not in (None, ""):
            raise ValueError("Never-launched runner unexpectedly assigned")
        # Direct lookup protects against a runner moved out of the expected group.
        current = api(f"{ORG}/runners/{runner_id}")
        if current is not None:
            if (not matches or not isinstance(current, dict) or type(current.get("id")) is not int
                    or current["id"] != runner_id or current.get("name") != name
                    or current.get("busy") is not False):
                raise ValueError("Direct runner identity/state mismatch")
            api(f"{ORG}/runners/{runner_id}", method="DELETE")
        if api(f"{ORG}/runners/{runner_id}") is not None or any(
            r.get("name") == name or r.get("id") == runner_id for r in inventory(api)
        ):
            raise RuntimeError("Runner deletion not independently confirmed")
        vm_inventory = list_vms()
        if not isinstance(vm_inventory, dict) or any(
            type(n) is not str or type(running) is not bool for n, running in vm_inventory.items()
        ):
            raise ValueError("Invalid fresh VM inventory")
        owned_names = vms.names(key)
        if any(n in vm_inventory for n in owned_names):
            raise RuntimeError("Owned VM reappeared; quarantine")
        processes_absent = owned_processes_absent(key)
        workspace_absent = owned_workspace_absent(key)
        if vms.pending() or processes_absent is not True or workspace_absent is not True:
            raise RuntimeError("VM/process cleanup remains pending")
        leases.resolve(key, {
            "lease_key": key, "runner_id": runner_id, "runner_name": name,
            "job_terminal": True, "runner_absent": True,
            "remote_processes_absent": processes_absent, "owned_workspace_absent": workspace_absent,
        })
        resolved.append(key)
    return resolved
