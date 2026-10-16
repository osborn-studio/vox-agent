"""Single-slot macOS controller service; routing stays disabled until approval.

Each repository has a separately approved main workflow. Both namespaces share
one journal and service lock. No Windows or release jobs are admitted.
"""

import base64
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from linux_dispatch_admission import admit, policy
from linux_dispatch_admission import REPOSITORY as REPOSITORY
from linux_dispatch_admission import REPOSITORY_ID as REPOSITORY_ID
from linux_dispatch_admission import WORKFLOW as WORKFLOW
from linux_dispatch_journal import LeaseJournal
from linux_dispatch_reconcile import reconcile_pending
from linux_dispatch_observer import GH, atomic_write, snapshot, trusted_config


ROOT = Path.home() / ".local/share/osvox-ci/runtime"
SSH_CONFIG = ROOT / "ssh/config"


def api(path, method="GET", body=None):
    command = [GH, "api", "--hostname", "github.com", path, "--method", method]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin",
            "GH_PROMPT_DISABLED": "1",
            "GH_PAGER": "cat",
        },
    )
    if result.returncode:
        raise RuntimeError("GitHub request failed")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def remote(namespace, nonce):
    if namespace not in ("osvox", "wiki"):
        raise ValueError("Unknown namespace")
    result = subprocess.run(
        [
            "/usr/bin/ssh",
            "-F",
            str(SSH_CONFIG),
            namespace + "-dmserver-runtime-ci",
            "reconcile " + nonce,
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode:
        raise RuntimeError("Remote runtime not quiescent")
    return json.loads(result.stdout)


def lease(run_id, config, *, namespace="osvox"):
    _, _, _, _, _, limit = policy(namespace)
    # No repository strings become shell commands. JIT configuration is stdin-only.
    with (ROOT / "logs" / ("service-lease-" + str(run_id) + "-" + uuid.uuid4().hex + ".log")).open(
        "x"
    ) as log:
        process = subprocess.Popen(
            [
                "/usr/bin/ssh",
                "-F",
                str(SSH_CONFIG),
                namespace + "-dmserver-runtime-ci",
                f"lease {run_id} {limit}",
            ],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
        )
        try:
            process.communicate(config.encode(), timeout=limit + 300)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
            raise
        if process.returncode:
            raise RuntimeError("Lease uncertain: preserve journal")


def verify_runner_group(request, *, namespace="osvox"):
    """Predictable labels are routing hints, never a workflow authorization boundary."""
    repository, repository_id, workflow, _, group_id, _ = policy(namespace)
    group = request(f"orgs/osborn-studio/actions/runner-groups/{group_id}")
    if (type(group.get("id")) is not int or group["id"] != group_id
            or group.get("visibility") != "selected"
            or group.get("restricted_to_workflows") is not True
            or group.get("selected_workflows") != [f"{repository}/{workflow}@refs/heads/main"]):
        raise RuntimeError("Runner group must restrict the exact main workflow")
    repos = request(f"orgs/osborn-studio/actions/runner-groups/{group_id}/repositories?per_page=100")
    entries = repos.get("repositories")
    if (type(repos.get("total_count")) is not int or repos["total_count"] != 1
            or not isinstance(entries, list) or len(entries) != 1
            or type(entries[0].get("id")) is not int
            or entries[0]["id"] != repository_id
            or entries[0].get("full_name") != repository):
        raise RuntimeError("Runner group must select only its approved repository")


def cycle(
    config, journal, request=api, reconcile_remote=remote, discover=snapshot, start_lease=lease,
    *, namespace="osvox"
):
    repository, _, workflow, _, group_id, _ = policy(namespace)
    # Preserve the original osvox adapter signature for existing deployments.
    options = {} if namespace == "osvox" else {"namespace": namespace}
    # Recover even when admission is disabled; never replay an uncertain lease.
    if journal.pending():
        reconcile_pending(journal, request, reconcile_remote)
        if journal.pending():
            return "QUARANTINED"
    if config.get("routing_approved") is not True:
        return "DISABLED"
    candidates = discover(config, **options)["jobs"]
    for candidate in candidates:
        if candidate.get("admitted") is not True:
            continue
        run_id, attempt, job_id = map(int, candidate["key"].split("-"))
        run = request(f"repos/{repository}/actions/runs/{run_id}")
        job = request(f"repos/{repository}/actions/jobs/{job_id}")
        raw = request(f"repos/{repository}/contents/{workflow}?ref={run['head_sha']}")
        accepted, _ = admit(
            run, job, base64.b64decode(raw["content"]), config.get("workflow_sha256"), True,
            **options
        )
        if not accepted or run["run_attempt"] != attempt:
            continue
        verify_runner_group(request, **options)
        name = namespace + "-prod-" + uuid.uuid4().hex
        journal.reserve(candidate["key"], name)
        jit = request(
            "orgs/osborn-studio/actions/runners/generate-jitconfig",
            method="POST",
            body={
                "name": name,
                "runner_group_id": group_id,
                "labels": job["labels"],
                "work_folder": "_work",
            },
        )
        journal.registered(candidate["key"], jit["runner"]["id"])
        journal.launch_intent(candidate["key"])
        start_lease(run_id, jit["encoded_jit_config"], **options)
        del jit
        reconcile_pending(journal, request, reconcile_remote)
        return "LEASE_PROCESSED"
    return "IDLE"


def shared_cycle(configs, journal, order=("osvox", "wiki"), run_cycle=cycle):
    """At most one lease per turn, with a shared quarantine across repositories."""
    states = {}
    for namespace in order:
        try:
            states[namespace] = run_cycle(configs[namespace], journal, namespace=namespace)
        except Exception as error:
            # A failed namespace cannot conceal an uncertain lease from the next one.
            states[namespace] = "ERROR_FAIL_CLOSED"
            if journal.pending():
                return {"state": "QUARANTINED", "namespaces": states,
                        "error_type": type(error).__name__}
        if journal.pending() or states[namespace] == "LEASE_PROCESSED":
            return {"state": states[namespace], "namespaces": states}
    # Retain DISABLED for the existing recovery harness when both policies are off.
    state = "DISABLED" if all(v == "DISABLED" for v in states.values()) else "IDLE"
    if "ERROR_FAIL_CLOSED" in states.values():
        state = "ERROR_FAIL_CLOSED"
    return {"state": state, "namespaces": states}



def load_configs(root):
    """A broken policy disables its own admission, never the other repository."""
    configs, errors = {}, {}
    for namespace, filename in (("osvox", "dispatch-admission.json"),
                                ("wiki", "wiki-admission.json")):
        try:
            configs[namespace] = trusted_config(root / "controller" / filename)
        except (RuntimeError, ValueError, TypeError, OSError) as error:
            configs[namespace] = {}
            errors[namespace] = type(error).__name__
    return configs, errors


def main():
    os.umask(0o077)
    for directory in (ROOT, ROOT / "controller", ROOT / "evidence", ROOT / "logs"):
        st = directory.stat()
        if (
            directory.resolve() != directory.absolute()
            or st.st_uid != os.getuid()
            or st.st_mode & 0o022
        ):
            raise RuntimeError("Untrusted controller directory")
    with (ROOT / "controller/service.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = LeaseJournal(ROOT / "controller/service.sqlite")
        order = ("osvox", "wiki")
        while True:
            try:
                # Recovery must not depend on either repository's config parsing.
                if journal.pending():
                    reconcile_pending(journal, api, remote)
                configs, config_errors = load_configs(ROOT)
                result = {
                    **shared_cycle(configs, journal, order),
                    "config_errors": config_errors,
                    "routing_approved": configs["osvox"].get("routing_approved") is True,
                    "wiki_routing_approved": configs["wiki"].get("routing_approved") is True,
                }
                if config_errors and result["state"] in ("DISABLED", "IDLE"):
                    result["state"] = "ERROR_FAIL_CLOSED"
            except Exception as error:
                # Do not log credentials, remote output or JIT configuration.
                result = {"state": "ERROR_FAIL_CLOSED", "error_type": type(error).__name__}
            atomic_write(
                ROOT / "evidence/dispatch-service.json",
                {
                    **result,
                    "observed_at": time.time(),
                    "pid": os.getpid(),
                    "pending_leases": len(journal.pending()),
                },
            )
            order = tuple(reversed(order))
            time.sleep(60)


if __name__ == "__main__":
    main()
