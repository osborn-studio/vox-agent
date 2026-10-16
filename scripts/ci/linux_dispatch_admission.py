"""Fail-closed admission for the trusted controller; never executes repository code.

An approved workflow blob is pinned separately from each PR's head. Changing a
workflow requires a fresh controller approval, not merely reusing a job name.
"""

import hashlib
import re

REPOSITORY = "osborn-studio/osvox"
REPOSITORY_ID = 1203204847
WORKFLOW = ".github/workflows/test-engine.yml"
JOBS = {"python-test (3.11)", "python-test (3.12)"}


def policy(namespace):
    """Fixed controller-owned policies; repository content cannot add destinations."""
    if namespace == "osvox":
        return REPOSITORY, REPOSITORY_ID, WORKFLOW, JOBS, 4, 1500
    if namespace == "wiki":
        return ("osborn-studio/osborn-wiki", 1375922457, ".github/workflows/ci.yml",
                {"backend-suite", "web-admission", "journal-native"}, 5, 2400)
    raise ValueError("Unknown runner namespace")


def admit(run, job, workflow_bytes, approved_sha256, enabled=False, *, namespace="osvox"):
    repository, repository_id, workflow, jobs, _, _ = policy(namespace)
    if enabled is not True:
        return False, "routing_disabled"
    repo = run.get("repository") or {}
    head_repo = run.get("head_repository") or {}
    if not isinstance(repo, dict) or not isinstance(head_repo, dict):
        return False, "invalid_repository_metadata"
    if repo.get("id") != repository_id or repo.get("full_name") != repository:
        return False, "wrong_repository"
    if head_repo.get("id") != repository_id:
        return False, "fork_requires_separate_isolation_policy"
    # PR merge-commit execution binding is not qualified yet. Fail closed.
    if run.get("event") != "push":
        return False, "wrong_event"
    if run.get("path") != workflow or run.get("status") not in {"queued", "in_progress"}:
        return False, "wrong_workflow_or_state"
    if run.get("event") == "push" and run.get("head_branch") != "main":
        return False, "non_main_push"
    if not re.fullmatch("[0-9a-f]{40}", str(run.get("head_sha", ""))):
        return False, "invalid_head"
    for field in ("id", "run_attempt"):
        if type(run.get(field)) is not int or run[field] < 1:
            return False, "invalid_run_identity"
    if (
        type(job.get("run_id")) is not int
        or type(job.get("run_attempt")) is not int
        or job.get("run_id") != run["id"]
        or job.get("run_attempt") != run["run_attempt"]
        or job.get("head_sha") != run["head_sha"]
    ):
        return False, "stale_job"
    if type(job.get("id")) is not int or job["id"] < 1:
        return False, "invalid_job_identity"
    if job.get("name") not in jobs or job.get("status") != "queued":
        return False, "wrong_job_or_state"
    version = job["name"].removeprefix("python-test (").removesuffix(")")
    expected = {
        "self-hosted",
        "Linux",
        "X64",
        "osvox-linux-python",
        f"osvox-run-{run['id']}",
        f"osvox-attempt-{run['run_attempt']}",
        f"osvox-python-{version}",
    }
    if namespace == "wiki":
        expected = {"self-hosted", "Linux", "X64", "wiki-linux-ci",
                    f"wiki-run-{run['id']}", f"wiki-attempt-{run['run_attempt']}",
                    f"wiki-job-{job['name']}"}
    labels = job.get("labels", [])
    if not isinstance(labels, list) or any(not isinstance(x, str) for x in labels):
        return False, "invalid_labels"
    if set(labels) != expected or len(labels) != len(expected):
        return False, "wrong_labels"
    if not re.fullmatch("[0-9a-f]{64}", str(approved_sha256)):
        return False, "missing_workflow_approval"
    if hashlib.sha256(workflow_bytes).hexdigest() != approved_sha256:
        return False, "workflow_changed"
    return True, "admitted"


def lease_key(run, job):
    """Attempt/job identity prevents retrying an uncertain lease after restart."""
    return f"{run['id']}-{run['run_attempt']}-{job['id']}"
