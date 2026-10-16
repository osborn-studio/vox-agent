import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/ci'))
spec = importlib.util.spec_from_file_location(
    'macos_runtime_rpc', Path(__file__).resolve().parents[2] / 'scripts/ci/macos_runtime_rpc.py'
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NONCE = 'a' * 32


@pytest.fixture
def rpc(tmp_path):
    tmp_path.chmod(0o700)
    vms = m.VMJournal(tmp_path / 'vms.sqlite')
    host = SimpleNamespace(list_vms=lambda: {}, processes_absent=lambda n: True,
                           workspace_absent=lambda n: True,
                           stop=lambda n: pytest.fail(), delete=lambda n: pytest.fail(),
                           instance_identity=lambda n: '1:2', home=tmp_path)
    launcher = SimpleNamespace(launch=lambda key, jit: {'runner_exited': True})
    value = m.RuntimeRPC(host, vms, launcher, tmp_path / 'lock')
    yield value
    vms.close()


def test_fresh_nonce_and_independent_proof(rpc):
    assert rpc.handle({'op': 'inventory', 'nonce': NONCE}) == {'nonce': NONCE, 'value': {}}
    rpc.vms.reserve('10-2-30')
    reply = rpc.handle({'op': 'recover', 'nonce': NONCE, 'key': '10-2-30'})
    assert reply['nonce'] == NONCE
    assert rpc.handle({'op': 'state', 'nonce': NONCE, 'key': '10-2-30'})['value'] == 'vm_absent'
    assert rpc.handle({'op': 'proof', 'nonce': NONCE, 'key': '10-2-30'})['value'] == {
        'processes_absent': True, 'workspace_absent': True}


@pytest.mark.parametrize('message', [
    {'op': 'delete', 'nonce': NONCE, 'name': 'personal'},
    {'op': 'inventory', 'nonce': 'stale'},
    {'op': 'inventory', 'nonce': NONCE, 'path': '/Users/eva'},
    {'op': 'launch', 'nonce': NONCE, 'key': 'not-a-key', 'jit': 'synthetic'},
])
def test_untrusted_operations_never_reach_host(rpc, message):
    with pytest.raises(ValueError):
        rpc.handle(message)


def test_recovery_refuses_another_lease(rpc):
    rpc.vms.reserve('10-2-30')
    with pytest.raises(RuntimeError):
        rpc.handle({'op': 'recover', 'nonce': NONCE, 'key': '10-2-31'})
    assert rpc.vms.pending()


def test_missing_key_cannot_manufacture_absence(rpc):
    with pytest.raises(RuntimeError):
        rpc.handle({'op': 'proof', 'nonce': NONCE, 'key': '10-2-30'})


def test_session_outer_capacity_lock_covers_recovery(rpc):
    rpc.vms.reserve('10-2-30')
    with m.capacity_lock(rpc.lock):
        rpc.lock_held = True
        result = rpc.handle({'op': 'recover', 'nonce': NONCE, 'key': '10-2-30'})
        assert result['value'][0]['vm_absent'] is True


def test_unrecorded_stopped_probe_blocks_readiness(rpc):
    (rpc.host.home / 'vms' / m.BASE).mkdir(parents=True)
    rpc.host.list_vms = lambda: {'osvox-probe-' + 'b' * 32: False}
    assert rpc.handle({'op': 'ready', 'nonce': NONCE})['value'] is False


def test_prepare_records_vm_intent_before_registration(rpc):
    rpc.handle({'op': 'prepare', 'nonce': NONCE, 'key': '10-2-30'})
    assert rpc.vms.pending()[0][2:4] == ('10-2-30', 'prepared')
    rpc.handle({'op': 'recover', 'nonce': NONCE, 'key': '10-2-30'})
    assert rpc.handle({'op': 'proof', 'nonce': NONCE, 'key': '10-2-30'})['value']['workspace_absent']
