"""Trusted main-only Swift fleet dispatcher. All deployment gates default off."""

import base64
import argparse
import os
from pathlib import Path
import time

from linux_dispatch_observer import atomic_write, trusted_config
from macos_dispatch_admission import REPOSITORY, REPOSITORY_ID, admit, profile
from macos_fleet_dispatch import LeaseJournal, cycle, initialize, placement
from macos_fleet_dispatch import bind as bind
from macos_fleet_host import Host
from macos_github_api import GitHubAPI, ORG, REPO
from macos_vm_recovery import capacity_lock, private_directory

ROOT = Path("/Users/osborn/.local/state/osvox-macos-fleet")


def pages(api, prefix, field):
    for page in range(1, 11):
        response = api(prefix + str(page))
        rows = response.get(field)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Invalid GitHub paginated inventory")
        yield rows
        if len(rows) < 100:
            return
    raise RuntimeError("GitHub pagination incomplete")


def workflow(api, run, *, mode="production", host=None):
    path, _, _ = profile(mode, host)
    response = api(f"repos/{REPOSITORY}/contents/{path}?ref={run['head_sha']}")
    if (response.get("encoding") != "base64" or not isinstance(response.get("content"), str)
            or len(response["content"]) > 1024 * 1024):
        raise ValueError("Unconfirmed workflow source bytes")
    return base64.b64decode(response["content"].replace("\n", ""), validate=True)


def discover(api, pin, *, mode="production", host=None):
    path, branch, _ = profile(mode, host)
    candidates, seen = {}, set()
    for status in ("queued", "in_progress"):
        prefix = (f"{REPO}/workflows/{path.rsplit('/', 1)[1]}/runs?status={status}"
                  f"&event=push&branch={branch}&per_page=100&page=")
        for rows in pages(api, prefix, "workflow_runs"):
            for run in rows:
                if run.get("path") != path or run.get("event") != "push":
                    continue
                identity = (run["id"], run["run_attempt"])
                if identity in seen:
                    continue
                seen.add(identity)
                raw = workflow(api, run, mode=mode, host=host)
                jobs = []
                job_prefix = f"{REPO}/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100&page="
                for job_page in pages(api, job_prefix, "jobs"):
                    jobs.extend(job_page)
                swift = [job for job in jobs if job.get("name") == "swift-build"]
                if len(swift) > 1:
                    raise RuntimeError("Ambiguous Swift job identity")
                for job in swift:
                    if admit(run, job, raw, pin, True, mode=mode, host=host)[0]:
                        key = f"{run['id']}-{run['run_attempt']}-{job['id']}"
                        candidates[key] = {"key": key, "labels": job["labels"]}
    return list(candidates.values())


def verify_group(api, *, mode="production", host=None):
    path, branch, _ = profile(mode, host)
    group = api(f"{ORG}/runner-groups/3")
    if (type(group.get("id")) is not int or group["id"] != 3
            or group.get("visibility") != "selected"
            or group.get("restricted_to_workflows") is not True
            or group.get("allows_public_repositories") is not False
            or group.get("selected_workflows") != [f"{REPOSITORY}/{path}@refs/heads/{branch}"]):
        raise RuntimeError("Runner group does not restrict exact main Swift workflow")
    response = api(f"{ORG}/runner-groups/3/repositories?per_page=100")
    rows = response.get("repositories")
    if (type(response.get("total_count")) is not int or response["total_count"] != 1
            or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or type(rows[0].get("id")) is not int or rows[0]["id"] != REPOSITORY_ID
            or rows[0].get("full_name") != REPOSITORY):
        raise RuntimeError("Runner group repository scope changed")


def approve(api, candidate, pin, *, mode="production", host=None):
    run_id, attempt, job_id = map(int, candidate["key"].split("-"))
    run = api(f"{REPO}/runs/{run_id}")
    job = api(f"{REPO}/jobs/{job_id}")
    if (run.get("id") != run_id or run.get("run_attempt") != attempt
            or job.get("id") != job_id or not admit(run, job, workflow(api, run, mode=mode, host=host),
                                                     pin, True, mode=mode, host=host)[0]):
        return False
    verify_group(api, mode=mode, host=host)
    candidate["labels"] = job["labels"]
    return True


def turn(config, journal, hosts, api):
    if journal.pending():
        return cycle({}, journal, hosts)
    mode = config.get("phase", "production")
    fault = config.get("qualification_fault", "none")
    if fault not in ("none", "registration_response_loss", "api_unavailable_after_launch"):
        raise ValueError("Unknown qualification fault")
    if mode != "qualification" and fault != "none":
        raise ValueError("Production cannot inject qualification faults")
    host = None
    if mode == "qualification":
        if config.get("routing_approved") is True:
            raise ValueError("Qualification cannot enable production routing")
        if config.get("qualification_approved") is not True:
            return "DISABLED"
        host = config.get("qualification_host")
        profile(mode, host)
        if config.get("qualified_hosts") != [host]:
            raise ValueError("Qualification must bind one explicitly qualified local host")
        pin = config.get("qualification_workflow_sha256")
        config = dict(config, routing_approved=True)
    elif mode == "production":
        if config.get("routing_approved") is not True:
            return "DISABLED"
        variable = api(f"{REPO}/variables/OSVOX_MACOS_ISOLATED_RUNNERS")
        if variable.get("name") != "OSVOX_MACOS_ISOLATED_RUNNERS" or variable.get("value") != "true":
            return "DISABLED_GITHUB_FLAG"
        pin = config.get("workflow_sha256")
    else:
        raise ValueError("Unknown service phase")

    def register(name, labels):
        verify_group(api, mode=mode, host=host)
        jit = api(f"{ORG}/runners/generate-jitconfig", method="POST", body={
            "name": name, "runner_group_id": 3, "labels": labels, "work_folder": "_work",
        })
        if mode == "qualification" and fault == "registration_response_loss":
            del jit
            raise RuntimeError("Qualification registration response intentionally lost")
        return jit

    state = cycle(config, journal, hosts, discover=lambda: discover(api, pin, mode=mode, host=host),
                  approve=lambda candidate: approve(api, candidate, pin, mode=mode, host=host), register=register)
    if journal.pending() and state == "LEASE_STARTED":
        key = journal.pending()[0][0]
        target = hosts[placement(journal, key)]
        if mode == "qualification" and fault == "api_unavailable_after_launch":
            def unavailable(*args, **kwargs):
                raise OSError("Qualification API unavailable")
            target.api = unavailable
        target.recover(key)
        return "QUARANTINED" if journal.pending() else "LEASE_PROCESSED"
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    # Installation creates these private directories; startup cannot adopt a
    # user-readable tools directory or the active Linux controller journal.
    private_directory(ROOT)
    private_directory(ROOT / "evidence")
    api = GitHubAPI()
    with capacity_lock(ROOT / "fleet.lock"):
        journal = LeaseJournal(ROOT / "leases.sqlite")
        initialize(journal)
        try:
            while True:
                hosts = {name: Host(name, journal, api, Path(__file__).parent)
                         for name in ("studio", "mini")}
                result = {"observed_at": time.time(), "mode": "MAC_FLEET_DISPATCH"}
                try:
                    try:
                        config = trusted_config(ROOT / "admission.json")
                    except Exception as error:
                        config = {}
                        result["config_error_type"] = type(error).__name__
                    result["state"] = turn(config, journal, hosts, api)
                except Exception as error:
                    result.update(state="QUARANTINED" if journal.pending() else "ERROR_FAIL_CLOSED",
                                  error_type=type(error).__name__)
                finally:
                    for host in hosts.values():
                        try:
                            host.close()
                        except Exception as error:
                            host.error = "SESSION_CLOSE_" + type(error).__name__
                    result["host_errors"] = {n: h.error for n, h in hosts.items() if h.error}
                    result["pending"] = journal.pending()
                    atomic_write(ROOT / "evidence/status.json", result)
                if args.once:
                    print(result["state"])
                    break
                time.sleep(60)
        finally:
            journal.close()


if __name__ == "__main__":
    main()
