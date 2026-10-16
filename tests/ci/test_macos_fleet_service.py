import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/ci'))
spec = importlib.util.spec_from_file_location(
    'macos_fleet_service', Path(__file__).resolve().parents[2] / 'scripts/ci/macos_fleet_service.py'
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def github():
    raw = b'reviewed workflow'
    run = {'id': 10, 'run_attempt': 2, 'head_sha': 'a' * 40, 'event': 'push',
           'head_branch': 'main', 'path': '.github/workflows/test-engine.yml', 'status': 'in_progress',
           'repository': {'id': 1203204847, 'full_name': 'osborn-studio/osvox'},
           'head_repository': {'id': 1203204847}}
    job = {'id': 30, 'run_id': 10, 'run_attempt': 2, 'head_sha': 'a' * 40, 'name': 'swift-build',
           'status': 'queued', 'runner_id': 0, 'runner_name': '',
           'labels': ['self-hosted', 'macOS', 'ARM64', 'osvox-macos-swift', 'osvox-run-10', 'osvox-attempt-2']}
    group = {'id': 3, 'visibility': 'selected', 'restricted_to_workflows': True,
             'allows_public_repositories': False,
             'selected_workflows': ['osborn-studio/osvox/.github/workflows/test-engine.yml@refs/heads/main']}
    calls = []
    def request(path, **kw):
        calls.append(path)
        if '/contents/' in path:
            return {'encoding': 'base64', 'content': base64.b64encode(raw).decode()}
        if '/runner-groups/' in path and '/repositories' in path:
            return {'total_count': 1, 'repositories': [run['repository']]}
        if '/runner-groups/' in path:
            return group
        if '/workflows/' in path:
            return {'workflow_runs': [run] if 'status=in_progress' in path else []}
        if '/attempts/' in path:
            return {'jobs': [job]}
        if '/jobs/' in path:
            return job
        return run
    return request, run, job, group, calls, hashlib.sha256(raw).hexdigest()


def test_discovery_and_fresh_exact_group_approval(github):
    api, _, _, _, calls, pin = github
    candidates = m.discover(api, pin)
    assert len(candidates) == 1 and candidates[0]['key'] == '10-2-30'
    assert m.approve(api, candidates[0], pin) is True
    assert any('/runner-groups/3/repositories' in path for path in calls)


def test_job_changes_after_discovery_not_registered(github):
    api, _, job, _, _, pin = github
    candidate = m.discover(api, pin)[0]
    job['runner_id'] = 42
    assert m.approve(api, candidate, pin) is False


def test_trial_workflow_group_cannot_authorize_main(github):
    api, _, _, group, _, pin = github
    candidate = m.discover(api, pin)[0]
    group['selected_workflows'] = ['trial-workflow']
    with pytest.raises(RuntimeError):
        m.approve(api, candidate, pin)


def test_incomplete_run_inventory_fails_closed(github):
    api, run, _, _, _, pin = github
    def endless(path):
        if '/actions/workflows/' in path:
            return {'workflow_runs': [run] * 100}
        return api(path)
    with pytest.raises(RuntimeError):
        m.discover(endless, pin)


def test_pending_recovery_precedes_admission_and_flag_reads(tmp_path):
    tmp_path.chmod(0o700)
    journal = m.LeaseJournal(tmp_path / 'fleet.sqlite')
    m.initialize(journal)
    journal.reserve('10-2-30', 'osvox-prod-' + 'a' * 32)
    m.bind(journal, '10-2-30', 'studio')
    calls = []
    hosts = {'studio': SimpleNamespace(recover=lambda key: calls.append(key))}
    def forbidden(*args, **kwargs):
        pytest.fail('Admission API reached before recovery')
    try:
        assert m.turn({'routing_approved': True}, journal, hosts, forbidden) == 'QUARANTINED'
        assert calls == ['10-2-30']
    finally:
        journal.close()


def test_disabled_service_never_discovers_or_registers(tmp_path):
    tmp_path.chmod(0o700)
    journal = m.LeaseJournal(tmp_path / 'fleet.sqlite')
    m.initialize(journal)
    try:
        assert m.turn({}, journal, {}, lambda *a, **k: pytest.fail()) == 'DISABLED'
    finally:
        journal.close()


def test_qualification_does_not_read_or_enable_production_flag(tmp_path, github):
    tmp_path.chmod(0o700)
    api, run, job, group, calls, pin = github
    run.update(head_branch='codex/macos-fleet-qualification',
               path='.github/workflows/macos-fleet-qualification.yml')
    job['labels'] = ['self-hosted', 'macOS', 'ARM64', 'osvox-macos-qualification',
                     'osvox-run-10', 'osvox-attempt-2', 'osvox-host-mini']
    group['selected_workflows'] = ['osborn-studio/osvox/.github/workflows/macos-fleet-qualification.yml@refs/heads/codex/macos-fleet-qualification']
    def request(path, **kwargs):
        if kwargs.get('method') == 'POST':
            return {'runner': {'id': 42, 'name': kwargs['body']['name']}, 'encoded_jit_config': 'synthetic'}
        return api(path, **kwargs)
    journal = m.LeaseJournal(tmp_path / 'fleet.sqlite')
    m.initialize(journal)
    host = SimpleNamespace(ready=lambda: True, prepare=lambda key: None,
                           launch=lambda key, jit: None, recover=lambda key: None)
    config = {'phase': 'qualification', 'qualification_approved': True, 'qualification_host': 'mini',
              'qualified_hosts': ['mini'], 'qualification_workflow_sha256': pin, 'routing_approved': False}
    try:
        assert m.turn(config, journal, {'mini': host}, request) == 'QUARANTINED'
        assert journal.pending()
        assert not any('/variables/' in path for path in calls)
    finally:
        journal.close()


def test_qualification_cannot_coenable_production(tmp_path):
    tmp_path.chmod(0o700)
    journal = m.LeaseJournal(tmp_path / 'fleet.sqlite')
    m.initialize(journal)
    try:
        with pytest.raises(ValueError):
            m.turn({'phase': 'qualification', 'routing_approved': True}, journal, {}, lambda *a: pytest.fail())
    finally:
        journal.close()


def test_production_cannot_enable_qualification_faults(tmp_path):
    tmp_path.chmod(0o700)
    journal = m.LeaseJournal(tmp_path / 'fleet.sqlite')
    m.initialize(journal)
    try:
        with pytest.raises(ValueError):
            m.turn({'phase': 'production', 'qualification_fault': 'registration_response_loss'},
                   journal, {}, lambda *a: pytest.fail())
    finally:
        journal.close()


def test_qualification_response_loss_records_unknown_and_never_launches(tmp_path, github):
    tmp_path.chmod(0o700)
    api, run, job, group, calls, pin = github
    run.update(head_branch='codex/macos-fleet-qualification',
               path='.github/workflows/macos-fleet-qualification.yml')
    job['labels'] = ['self-hosted', 'macOS', 'ARM64', 'osvox-macos-qualification',
                     'osvox-run-10', 'osvox-attempt-2', 'osvox-host-mini']
    group['selected_workflows'] = ['osborn-studio/osvox/.github/workflows/macos-fleet-qualification.yml@refs/heads/codex/macos-fleet-qualification']
    effects = []
    def request(path, **kwargs):
        if kwargs.get('method') == 'POST':
            effects.append('registered')
            return {'runner': {'id': 42, 'name': kwargs['body']['name']}, 'encoded_jit_config': 'synthetic'}
        return api(path, **kwargs)
    journal = m.LeaseJournal(tmp_path / 'fleet.sqlite')
    m.initialize(journal)
    host = SimpleNamespace(ready=lambda: True, prepare=lambda key: effects.append('prepared'),
                           launch=lambda *a: pytest.fail('Lost response must not launch'))
    config = {'phase': 'qualification', 'qualification_approved': True, 'qualification_host': 'mini',
              'qualified_hosts': ['mini'], 'qualification_workflow_sha256': pin, 'routing_approved': False,
              'qualification_fault': 'registration_response_loss'}
    try:
        with pytest.raises(RuntimeError, match='lost'):
            m.turn(config, journal, {'mini': host}, request)
        assert effects == ['prepared', 'registered']
        assert journal.pending()[0][1:3] == ('registration_unknown', None)
    finally:
        journal.close()


def test_api_outage_hook_reaches_recovery_and_persists_quarantine(tmp_path, github, monkeypatch):
    tmp_path.chmod(0o700)
    (tmp_path / 'evidence').mkdir(mode=0o700)
    api, run, job, group, calls, pin = github
    run.update(head_branch='codex/macos-fleet-qualification',
               path='.github/workflows/macos-fleet-qualification.yml')
    job['labels'] = ['self-hosted', 'macOS', 'ARM64', 'osvox-macos-qualification',
                     'osvox-run-10', 'osvox-attempt-2', 'osvox-host-mini']
    group['selected_workflows'] = ['osborn-studio/osvox/.github/workflows/macos-fleet-qualification.yml@refs/heads/codex/macos-fleet-qualification']
    def request(path, **kwargs):
        if kwargs.get('method') == 'POST':
            return {'runner': {'id': 42, 'name': kwargs['body']['name']}, 'encoded_jit_config': 'synthetic'}
        return api(path, **kwargs)
    class Host:
        error = None
        def __init__(self, name, journal, request, source):
            self.api = request
        def ready(self):
            return True
        def prepare(self, key):
            pass
        def launch(self, key, jit):
            pass
        def recover(self, key):
            self.api('repos/osborn-studio/osvox/actions/jobs/30')
        def close(self):
            pass
    config = {'phase': 'qualification', 'qualification_approved': True, 'qualification_host': 'mini',
              'qualified_hosts': ['mini'], 'qualification_workflow_sha256': pin, 'routing_approved': False,
              'qualification_fault': 'api_unavailable_after_launch'}
    (tmp_path / 'admission.json').write_text(json.dumps(config))
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    monkeypatch.setattr(m, 'Host', Host)
    monkeypatch.setattr(m, 'GitHubAPI', lambda: request)
    monkeypatch.setattr(sys, 'argv', ['fleet', '--once'])
    m.main()
    result = json.loads((tmp_path / 'evidence/status.json').read_text())
    assert result['state'] == 'QUARANTINED' and result['error_type'] == 'OSError'
    assert result['pending'][0][1] == 'lease_unknown'
