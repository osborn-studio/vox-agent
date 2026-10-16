import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "admission", Path(__file__).parents[2] / "scripts/ci/linux_dispatch_admission.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def inputs():
    run = dict(
        id=123,
        run_attempt=1,
        repository=dict(id=m.REPOSITORY_ID, full_name=m.REPOSITORY),
        head_repository=dict(id=m.REPOSITORY_ID),
        event="push",
        head_branch="main",
        path=m.WORKFLOW,
        status="in_progress",
        head_sha="a" * 40,
    )
    job = dict(
        id=456,
        run_id=123,
        run_attempt=1,
        head_sha="a" * 40,
        name="python-test (3.11)",
        status="queued",
        labels=[
            "self-hosted",
            "Linux",
            "X64",
            "osvox-linux-python",
            "osvox-run-123",
            "osvox-attempt-1",
            "osvox-python-3.11",
        ],
    )
    return run, job


def check(run, job, enabled=True, content=b"approved"):
    return m.admit(run, job, content, hashlib.sha256(b"approved").hexdigest(), enabled)[0]


@pytest.mark.parametrize("version", ["3.11", "3.12"])
def test_exact_python_only(version):
    run, job = inputs()
    job["name"] = f"python-test ({version})"
    job["labels"][-1] = f"osvox-python-{version}"
    assert check(run, job)
    assert not check(run, job, enabled=False)
    assert not check(run, job, content=b"unreviewed workflow")


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "swift-build"),
        ("name", "windows-tauri-test"),
        ("name", "release"),
        ("status", "in_progress"),
        ("run_attempt", 2),
        ("run_id", 124),
        ("head_sha", "b" * 40),
        ("id", True),
        ("labels", ["ubuntu-latest"]),
        ("labels", ["self-hosted", "Linux", "X64", "osvox-linux-python", "osvox-run-124"]),
    ],
)
def test_reject_other_or_stale_job(field, value):
    run, job = inputs()
    job[field] = value
    assert not check(run, job)


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_repository", None),
        ("repository", None),
        ("head_repository", []),
        ("event", "pull_request"),
        ("event", "pull_request_target"),
        ("event", "workflow_dispatch"),
        ("path", ".github/workflows/release-dmg.yml"),
        ("status", "completed"),
        ("id", True),
        ("head_repository", dict(id=99)),
        ("repository", dict(id=99, full_name=m.REPOSITORY)),
    ],
)
def test_reject_untrusted_run(field, value):
    run, job = inputs()
    run[field] = value
    assert not check(run, job)


def test_main_push_and_journal_attempt_binding():
    run, job = inputs()
    run.update(event="push", head_branch="main")
    assert check(run, job)
    run["head_branch"] = "another-branch"
    assert not check(run, job)
    retry = copy.deepcopy(run)
    retry["run_attempt"] = 2
    assert m.lease_key(run, job) != m.lease_key(retry, job)


def test_matrix_version_and_retry_labels_are_bound():
    run, job = inputs()
    job["labels"][-1] = "osvox-python-3.12"
    assert not check(run, job)
    run, job = inputs()
    job["labels"][-2] = "osvox-attempt-2"
    assert not check(run, job)
