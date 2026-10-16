"""Wiki routing must not weaken the shared controller's admission or quarantine."""
import base64
import hashlib
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / 'scripts/ci'))
import linux_dispatch_admission as admission
import linux_dispatch_observer as observer
import linux_dispatch_service as service
from linux_dispatch_journal import LeaseJournal

WORKFLOW_BYTES = b'approved wiki workflow'
HASH = hashlib.sha256(WORKFLOW_BYTES).hexdigest()
REPO, REPO_ID, WORKFLOW, JOBS, GROUP, _ = admission.policy('wiki')


def inputs(name='backend-suite'):
    run = dict(id=123, run_attempt=2, head_sha='a' * 40, event='push',
               head_branch='main', path=WORKFLOW, status='in_progress',
               repository=dict(id=REPO_ID, full_name=REPO),
               head_repository=dict(id=REPO_ID))
    job = dict(id=456, run_id=123, run_attempt=2, head_sha='a' * 40,
               name=name, status='queued', labels=[
                   'self-hosted', 'Linux', 'X64', 'wiki-linux-ci',
                   'wiki-run-123', 'wiki-attempt-2', f'wiki-job-{name}'])
    return run, job


def accepted(run, job, enabled=True, content=WORKFLOW_BYTES):
    return admission.admit(run, job, content, HASH, enabled, namespace='wiki')[0]


@pytest.mark.parametrize('name', sorted(JOBS))
def test_all_generic_jobs_require_own_labels_and_reviewed_bytes(name):
    run, job = inputs(name)
    assert accepted(run, job)
    assert not accepted(run, job, enabled=False)
    assert not accepted(run, job, enabled='true')
    assert not accepted(run, job, content=b'changed')
    assert not admission.admit(run, job, WORKFLOW_BYTES, HASH, True)[0]
    job['labels'][-1] = 'wiki-job-verify'
    assert not accepted(run, job)


@pytest.mark.parametrize('field,value', [
    ('event', 'pull_request'), ('event', 'pull_request_target'),
    ('event', 'workflow_dispatch'), ('head_branch', 'feature'),
    ('head_repository', {'id': 99}), ('repository', {'id': REPO_ID, 'full_name': 'other'}),
    ('path', '.github/workflows/publish.yml'), ('run_attempt', True),
    ('status', 'completed'), ('head_sha', 'not-a-sha'),
])
def test_unqualified_events_and_identities_never_admit(field, value):
    run, job = inputs()
    run[field] = value
    assert not accepted(run, job)


@pytest.mark.parametrize('field,value', [
    ('name', 'verify'), ('name', 'windows-distribution'), ('name', 'release'),
    ('name', 'python-test (3.11)'), ('id', True), ('run_attempt', 1),
    ('head_sha', 'b' * 40), ('run_id', 777), ('status', 'in_progress'),
    ('labels', ['ubuntu-latest']),
])
def test_wrong_or_stale_jobs_never_admit(field, value):
    run, job = inputs()
    job[field] = value
    assert not accepted(run, job)


def test_namespace_is_fixed_and_retry_labels_cannot_be_reused():
    with pytest.raises(ValueError):
        admission.policy('untrusted')
    run, job = inputs()
    job['labels'].append(job['labels'][0])
    assert not accepted(run, job)
    run, job = inputs()
    run['run_attempt'] = job['run_attempt'] = 3
    assert not accepted(run, job)


def group_responses():
    return [dict(id=GROUP, visibility='selected', restricted_to_workflows=True,
                 selected_workflows=[f'{REPO}/{WORKFLOW}@refs/heads/main']),
            dict(total_count=1, repositories=[dict(id=REPO_ID, full_name=REPO)])]


@pytest.mark.parametrize('bad', ['group', 'workflow', 'repository', 'unrestricted'])
def test_wiki_group_cannot_use_osvox_or_broad_access(bad):
    values = group_responses()
    if bad == 'group':
        values[0]['id'] = 4
    elif bad == 'workflow':
        values[0]['selected_workflows'] = ['osborn-studio/osvox/.github/workflows/test-engine.yml@refs/heads/main']
    elif bad == 'repository':
        values[1]['repositories'][0]['id'] = admission.REPOSITORY_ID
    else:
        values[0]['restricted_to_workflows'] = False
    with pytest.raises(RuntimeError):
        service.verify_runner_group(Mock(side_effect=values), namespace='wiki')


def test_wiki_discovery_queries_only_its_workflow(monkeypatch):
    run, job = inputs()
    paths = []

    def request(path):
        paths.append(path)
        if '/contents/' in path:
            return {'content': base64.b64encode(WORKFLOW_BYTES).decode()}
        if '/attempts/' in path:
            return {'jobs': [job]}
        return {'workflow_runs': [run] if 'status=in_progress' in path else []}

    monkeypatch.setattr(observer, 'api', request)
    result = observer.snapshot({'routing_approved': True, 'workflow_sha256': HASH}, namespace='wiki')
    assert result['jobs'] == [{'key': '123-2-456', 'name': 'backend-suite', 'admitted': True, 'reason': 'admitted'}]
    assert all(p.startswith(f'repos/{REPO}/') for p in paths)
    assert any('/workflows/ci.yml/runs?' in p for p in paths)


def test_wiki_lease_uses_own_identity_and_shared_durable_launch(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    journal = LeaseJournal(tmp_path / 'journal.sqlite')
    run, job = inputs()
    request = Mock(side_effect=[run, job, {'content': base64.b64encode(WORKFLOW_BYTES).decode()},
                               *group_responses(), {'runner': {'id': 90}, 'encoded_jit_config': 'synthetic'}])
    discover = Mock(return_value={'jobs': [{'key': '123-2-456', 'admitted': True}]})
    monkeypatch.setattr(service, 'reconcile_pending', lambda *args: [])

    def lease(run_id, encoded, *, namespace):
        assert (run_id, encoded, namespace) == (123, 'synthetic', 'wiki')
        reopened = LeaseJournal(tmp_path / 'journal.sqlite')
        try:
            row = reopened.pending()[0]
            assert row[:3] == ('123-2-456', 'lease_unknown', 90)
            assert row[3].startswith('wiki-prod-')
            with pytest.raises(RuntimeError, match='Unresolved'):
                reopened.reserve('999-1-999', 'osvox-prod-' + 'b' * 32)
        finally:
            reopened.close()

    try:
        assert service.cycle({'routing_approved': True, 'workflow_sha256': HASH}, journal,
                             request, Mock(), discover, lease, namespace='wiki') == 'LEASE_PROCESSED'
        registration = request.call_args_list[-1].kwargs['body']
        assert registration['runner_group_id'] == 5
        assert registration['labels'] == job['labels']
        assert registration['name'].startswith('wiki-prod-')
    finally:
        journal.close()


def test_shared_cycle_stops_at_unknown_lease_even_on_exception(tmp_path):
    tmp_path.chmod(0o700)
    journal = LeaseJournal(tmp_path / 'journal.sqlite')
    seen = []

    def run(config, journal, *, namespace):
        seen.append(namespace)
        journal.reserve('123-2-456', 'wiki-prod-' + 'a' * 32)
        raise RuntimeError('registration result unknown')

    try:
        result = service.shared_cycle({'wiki': {}, 'osvox': {}}, journal,
                                      order=('wiki', 'osvox'), run_cycle=run)
        assert result['state'] == 'QUARANTINED'
        assert seen == ['wiki']
    finally:
        journal.close()


def test_both_disabled_and_single_dispatch_per_turn(tmp_path):
    tmp_path.chmod(0o700)
    journal = LeaseJournal(tmp_path / 'journal.sqlite')
    configs = {'osvox': {}, 'wiki': {}}
    try:
        assert service.shared_cycle(configs, journal)['state'] == 'DISABLED'
        run = Mock(return_value='LEASE_PROCESSED')
        assert service.shared_cycle(configs, journal, ('wiki', 'osvox'), run)['state'] == 'LEASE_PROCESSED'
        assert run.call_count == 1 and run.call_args.kwargs['namespace'] == 'wiki'
    finally:
        journal.close()


def test_wiki_ssh_endpoint_limit_and_stdin_only_credentials(tmp_path, monkeypatch):
    (tmp_path / 'logs').mkdir()
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    process = Mock(returncode=0)
    popen = Mock(return_value=process)
    monkeypatch.setattr(service.subprocess, 'Popen', popen)
    service.lease(123, 'synthetic-jit', namespace='wiki')
    args = popen.call_args.args[0]
    assert args[-2:] == ['wiki-dmserver-runtime-ci', 'lease 123 2400']
    assert 'synthetic-jit' not in ' '.join(args)
    process.communicate.assert_called_once_with(b'synthetic-jit', timeout=2700)


def test_main_recovers_before_parsing_broken_namespace_config(tmp_path, monkeypatch):
    for directory in (tmp_path, tmp_path / 'controller', tmp_path / 'logs', tmp_path / 'evidence'):
        directory.mkdir(exist_ok=True)
        directory.chmod(0o700)
    journal = LeaseJournal(tmp_path / 'controller/service.sqlite')
    journal.reserve('123-2-456', 'osvox-prod-' + 'a' * 32)
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    monkeypatch.setattr(service, 'LeaseJournal', lambda path: journal)
    events = []
    monkeypatch.setattr(service, 'reconcile_pending', lambda *args: events.append('recover'))

    def config(path):
        events.append(path.name)
        if path.name == 'wiki-admission.json':
            raise ValueError('malformed wiki config')
        return {}

    class EndLoop(BaseException):
        pass

    monkeypatch.setattr(service, 'trusted_config', config)
    shared = Mock(return_value={'state': 'DISABLED'})
    monkeypatch.setattr(service, 'shared_cycle', shared)
    monkeypatch.setattr(service.time, 'sleep', Mock(side_effect=EndLoop))
    try:
        with pytest.raises(EndLoop):
            service.main()
        assert events == ['recover', 'dispatch-admission.json', 'wiki-admission.json']
        import json
        state = json.loads((tmp_path / 'evidence/dispatch-service.json').read_text())
        assert state['state'] == 'ERROR_FAIL_CLOSED' and state['pending_leases'] == 1
        assert state['config_errors'] == {'wiki': 'ValueError'}
        assert shared.call_args.args[0] == {'osvox': {}, 'wiki': {}}
    finally:
        journal.close()


def test_bad_wiki_config_does_not_block_osvox_admission(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    (tmp_path / 'controller').mkdir(mode=0o700)
    (tmp_path / 'controller/wiki-admission.json').write_text('{invalid')
    (tmp_path / 'controller/dispatch-admission.json').write_text('{"routing_approved": true}')
    configs, errors = service.load_configs(tmp_path)
    assert configs == {'osvox': {'routing_approved': True}, 'wiki': {}}
    assert errors == {'wiki': 'JSONDecodeError'}
    journal = LeaseJournal(tmp_path / 'journal.sqlite')
    try:
        def run(config, journal, *, namespace):
            return 'LEASE_PROCESSED' if config.get('routing_approved') else 'DISABLED'
        result = service.shared_cycle(configs, journal, ('wiki', 'osvox'), run)
        assert result['namespaces'] == {'wiki': 'DISABLED', 'osvox': 'LEASE_PROCESSED'}
    finally:
        journal.close()
