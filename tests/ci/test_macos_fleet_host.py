import importlib.util
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/ci'))
spec = importlib.util.spec_from_file_location(
    'macos_fleet_host', Path(__file__).resolve().parents[2] / 'scripts/ci/macos_fleet_host.py'
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
LeaseJournal = importlib.import_module('linux_dispatch_journal').LeaseJournal


def test_remote_host_lock_spans_vm_and_github_recovery(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    journal = LeaseJournal(tmp_path / 'fleet.sqlite')
    key, name, vm = '10-2-30', 'osvox-prod-' + 'a' * 32, 'osvox-probe-' + 'b' * 32
    journal.reserve(key, name)
    journal.registered(key, 42)
    journal.launch_intent(key)
    calls = []
    state = {'running': True, 'runner': {'id': 42, 'name': name, 'busy': False}}
    class Session:
        closed = False
        def __init__(self, command):
            self.command = command
        def call(self, op, **kwargs):
            assert not self.closed
            calls.append(op)
            if op == 'pending':
                return [[vm, 'digest', key, 'owned', '1:2']] if state['running'] else []
            if op == 'recover':
                state['running'] = False
                return []
            if op == 'state':
                return 'vm_absent'
            if op == 'names':
                return [vm]
            if op == 'inventory':
                return {}
            if op == 'proof':
                return {'processes_absent': True, 'workspace_absent': True}
            raise AssertionError(op)
        def close(self):
            self.closed = True
    monkeypatch.setattr(m, 'Session', Session)
    def api(path, method='GET'):
        assert host.session and not host.session.closed
        calls.append(method + ':' + path)
        if '/jobs/' in path:
            return {'id': 30, 'run_id': 10, 'run_attempt': 2, 'name': 'swift-build',
                    'status': 'completed', 'runner_id': 42, 'runner_name': name}
        if '/runner-groups/' in path:
            return {'runners': [state['runner']] if state['runner'] else []}
        if method == 'DELETE':
            state['runner'] = None
        return state['runner']
    host = m.Host('studio', journal, api, '/trusted/service')
    try:
        assert host.recover(key) == [key]
        assert not journal.pending()
        assert calls.index('recover') < next(i for i, c in enumerate(calls) if c.startswith('DELETE:'))
        assert host.session.closed is False
        host.close()
    finally:
        journal.close()
