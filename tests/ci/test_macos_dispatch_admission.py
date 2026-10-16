import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'macos_dispatch_admission', Path(__file__).resolve().parents[2] / 'scripts/ci/macos_dispatch_admission.py'
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def inputs():
    run = {'repository': {'id': 1203204847, 'full_name': 'osborn-studio/osvox'},
           'head_repository': {'id': 1203204847}, 'event': 'push', 'path': '.github/workflows/test-engine.yml',
           'status': 'in_progress', 'head_branch': 'main', 'head_sha': 'a' * 40,
           'id': 10, 'run_attempt': 2}
    job = {'id': 30, 'run_id': 10, 'run_attempt': 2, 'head_sha': 'a' * 40,
           'name': 'swift-build', 'status': 'queued', 'runner_id': 0, 'runner_name': '',
           'labels': ['self-hosted', 'macOS', 'ARM64', 'osvox-macos-swift', 'osvox-run-10', 'osvox-attempt-2']}
    return run, job, b'reviewed workflow', hashlib.sha256(b'reviewed workflow').hexdigest()


def test_exact_main_swift_job_only():
    assert m.admit(*inputs(), enabled=True) == (True, 'admitted')
    assert not m.admit(*inputs(), enabled=False)[0]


@pytest.mark.parametrize('surface,field,value', [
    ('run', 'event', 'pull_request'), ('run', 'head_branch', 'feature'),
    ('run', 'id', True), ('run', 'head_repository', {'id': 1}),
    ('job', 'name', 'windows-tauri-test'), ('job', 'run_attempt', 1),
    ('job', 'labels', ['self-hosted']), ('job', 'runner_id', 42),
    ('job', 'status', 'in_progress'), ('job', 'head_sha', 'b' * 40),
])
def test_stale_fork_pr_wrong_platform_or_assigned_job_rejected(surface, field, value):
    run, job, raw, pin = inputs()
    (run if surface == 'run' else job)[field] = value
    assert not m.admit(run, job, raw, pin, enabled=True)[0]


def test_changed_workflow_or_truthy_enable_rejected():
    run, job, raw, pin = inputs()
    assert not m.admit(run, job, raw + b'changed', pin, enabled=True)[0]
    assert not m.admit(run, job, raw, pin, enabled='true')[0]


def test_qualification_policy_is_explicit_and_cannot_admit_main_or_other_host():
    run, job, raw, pin = inputs()
    run['head_branch'] = 'codex/macos-fleet-qualification'
    run['path'] = '.github/workflows/macos-fleet-qualification.yml'
    job['labels'] = ['self-hosted', 'macOS', 'ARM64', 'osvox-macos-qualification',
                     'osvox-run-10', 'osvox-attempt-2', 'osvox-host-mini']
    assert m.admit(run, job, raw, pin, True, mode='qualification', host='mini')[0]
    assert not m.admit(run, job, raw, pin, True)[0]
    assert not m.admit(run, job, raw, pin, True, mode='qualification', host='studio')[0]
    run['head_branch'] = 'main'
    assert not m.admit(run, job, raw, pin, True, mode='qualification', host='mini')[0]
