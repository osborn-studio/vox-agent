"""Exact main-only Swift admission; labels alone never authorize execution."""

import hashlib
import re


REPOSITORY = "osborn-studio/osvox"
REPOSITORY_ID = 1203204847
WORKFLOW = ".github/workflows/test-engine.yml"
QUALIFICATION_WORKFLOW = ".github/workflows/macos-fleet-qualification.yml"
QUALIFICATION_BRANCH = "codex/macos-fleet-qualification"


def profile(mode="production", host=None):
    if mode == "production":
        return WORKFLOW, "main", "osvox-macos-swift"
    if mode == "qualification" and host in ("mini", "studio"):
        return QUALIFICATION_WORKFLOW, QUALIFICATION_BRANCH, "osvox-macos-qualification"
    raise ValueError("Unknown trusted admission policy")


def admit(run, job, workflow_bytes, approved_sha256, enabled=False, *, mode="production", host=None):
    if enabled is not True:
        return False, "routing_disabled"
    if not isinstance(run, dict) or not isinstance(job, dict):
        return False, "invalid_metadata"
    workflow, branch, label = profile(mode, host)
    repo, head = run.get("repository"), run.get("head_repository")
    if (not isinstance(repo, dict) or not isinstance(head, dict)
            or repo.get("id") != REPOSITORY_ID or repo.get("full_name") != REPOSITORY
            or head.get("id") != REPOSITORY_ID):
        return False, "wrong_repository_or_fork"
    if (run.get("event") != "push" or run.get("head_branch") != branch
            or run.get("path") != workflow or run.get("status") not in ("queued", "in_progress")
            or not re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha", "")))):
        return False, "wrong_workflow_event_or_head"
    if any(type(run.get(k)) is not int or run[k] < 1 for k in ("id", "run_attempt")):
        return False, "invalid_run_identity"
    if (type(job.get("id")) is not int or job["id"] < 1
            or any(type(job.get(k)) is not int or job[k] != run[r]
                   for k, r in (("run_id", "id"), ("run_attempt", "run_attempt")))
            or job.get("head_sha") != run["head_sha"]):
        return False, "stale_job"
    if (job.get("name") != "swift-build" or job.get("status") != "queued"
            or job.get("runner_name") not in (None, "")
            or job.get("runner_id") is not None and
            (type(job["runner_id"]) is not int or job["runner_id"] != 0)):
        return False, "wrong_or_assigned_job"
    expected = {"self-hosted", "macOS", "ARM64", label,
                f"osvox-run-{run['id']}", f"osvox-attempt-{run['run_attempt']}"}
    if mode == "qualification":
        expected.add("osvox-host-" + host)
    labels = job.get("labels")
    if (not isinstance(labels, list) or any(type(x) is not str for x in labels)
            or len(labels) != len(expected) or set(labels) != expected):
        return False, "wrong_labels"
    if (not re.fullmatch(r"[0-9a-f]{64}", str(approved_sha256))
            or hashlib.sha256(workflow_bytes).hexdigest() != approved_sha256):
        return False, "missing_or_changed_workflow_approval"
    return True, "admitted"
