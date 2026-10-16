"""Reconcile durable leases using independent GitHub and forced-SSH evidence.

No retries or new runner registrations occur here. api and remote are trusted
controller adapters, never values supplied by a repository or workflow.
"""

import re
import uuid

REPOSITORIES = {
    "osvox": ("osborn-studio/osvox", 4, "/var/lib/osvox-ci"),
    "wiki": ("osborn-studio/osborn-wiki", 5, "/var/lib/osborn-wiki-ci"),
}


def runners(api, group):
    result = []
    for page in range(1, 11):
        values = api(f"orgs/osborn-studio/actions/runner-groups/{group}/runners?per_page=100&page={page}")["runners"]
        if not isinstance(values, list):
            raise ValueError("Invalid runner inventory")
        result.extend(values)
        if len(values) < 100:
            return result
    raise RuntimeError("Runner inventory incomplete; keep lease quarantined")


def reconcile_pending(journal, api, remote):
    resolved = []
    for key, state, runner_id, name in journal.pending():
        match = re.fullmatch(r"(osvox|wiki)-prod-[0-9a-f]{32}", name)
        if not match:
            raise ValueError("Unknown runner namespace")
        namespace = match[1]
        repository, group, root = REPOSITORIES[namespace]
        run_id, attempt, job_id = map(int, key.split("-"))
        job = api(f"repos/{repository}/actions/jobs/{job_id}")
        if any(type(job.get(field)) is not int or job[field] != expected
               for field, expected in (("id", job_id), ("run_id", run_id), ("run_attempt", attempt))):
            raise ValueError("Job identity mismatch")
        if job.get("status") != "completed":
            continue
        inventory = runners(api, group)
        owned = [r for r in inventory if r.get("name") == name or r.get("id") == runner_id]
        if len(owned) > 1:
            raise ValueError("Ambiguous runner identity")
        if owned:
            runner = owned[0]
            if (runner.get("name") != name or type(runner.get("id")) is not int
                    or runner["id"] < 1 or runner.get("busy") is not False
                    or (runner_id is not None and runner["id"] != runner_id)):
                raise ValueError("Runner identity/state mismatch")
            if runner_id is None:
                runner_id = runner["id"]
                journal.registered(key, runner_id)
        assigned_id, assigned_name = job.get("runner_id"), job.get("runner_name")
        if state == "lease_unknown":
            if (type(assigned_id) is not int or assigned_id != runner_id
                    or assigned_name != name):
                raise ValueError("Started lease lacks exact job assignment; operator reconciliation required")
        elif (assigned_id is not None and (type(assigned_id) is not int or assigned_id != 0)) or assigned_name not in (None, ""):
            raise ValueError("Never-launched registration unexpectedly assigned to a runner")
        nonce = uuid.uuid4().hex
        proof = remote(namespace, nonce)
        if (proof.get("status") != "QUIESCENT_RECONCILED" or proof.get("nonce") != nonce
                or proof.get("root") != root or proof.get("remote_processes_absent") is not True
                or proof.get("owned_workspace_absent") is not True
                or type(proof.get("recovery", {}).get("workspaces_remaining")) is not int
                or proof["recovery"]["workspaces_remaining"] != 0):
            raise ValueError("Incomplete or replayed remote proof")
        if owned:
            api(f"orgs/osborn-studio/actions/runners/{runner_id}", method="DELETE")
        if any(r.get("name") == name or (runner_id is not None and r.get("id") == runner_id)
               for r in runners(api, group)):
            raise RuntimeError("Runner removal not confirmed")
        journal.resolve(key, {
            "lease_key": key, "runner_id": runner_id, "runner_name": name,
            "job_terminal": True, "runner_absent": True,
            "remote_processes_absent": True, "owned_workspace_absent": True,
        })
        resolved.append(key)
    return resolved
