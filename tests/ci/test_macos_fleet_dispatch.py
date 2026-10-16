import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    'macos_fleet_dispatch', Path(__file__).resolve().parents[2] / 'scripts/ci/macos_fleet_dispatch.py'
)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/ci'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def case(tmp_path):
    tmp_path.chmod(0o700)
    journal = m.LeaseJournal(tmp_path / 'fleet.sqlite')
    m.initialize(journal)
    calls = []
    hosts = {}
    for host_id in ('studio', 'mini'):
        hosts[host_id] = SimpleNamespace(
            ready=lambda: True,
            prepare=lambda key, h=host_id: calls.append(('prepare', h, key)),
            recover=lambda key, h=host_id: calls.append(('recover', h, key)),
            launch=lambda key, jit, h=host_id: calls.append(('launch', h, key, jit)),
        )
    yield journal, hosts, calls
    journal.close()


def config():
    return {'routing_approved': True, 'qualified_hosts': ['studio', 'mini']}


def candidate():
    return {'key': '10-2-30', 'labels': ['trusted-label']}


def test_studio_priority_and_mini_fallback_with_single_global_slot(case):
    journal, hosts, calls = case
    hosts['studio'].ready = lambda: False
    assert m.cycle(config(), journal, hosts, discover=lambda: [candidate()],
                   approve=lambda c: True, register=lambda name, labels:
                   {'runner': {'id': 42, 'name': name}, 'encoded_jit_config': 'synthetic'}) == 'LEASE_STARTED'
    assert calls[0][:2] == ('prepare', 'mini')
    assert calls[1][:2] == ('launch', 'mini')
    assert m.placement(journal, '10-2-30') == 'mini'
    assert journal.pending()[0][1] == 'lease_unknown'


def test_disabled_routing_still_recovers_on_exact_bound_host(case):
    journal, hosts, calls = case
    journal.reserve('10-2-30', 'osvox-prod-' + 'a' * 32)
    m.bind(journal, '10-2-30', 'studio')
    assert m.cycle({}, journal, hosts) == 'QUARANTINED'
    assert calls == [('recover', 'studio', '10-2-30')]


def test_unknown_registration_never_replays_or_falls_back(case):
    journal, hosts, calls = case
    attempts = []

    def register(name, labels):
        attempts.append(name)
        raise OSError('response lost')

    with pytest.raises(OSError):
        m.cycle(config(), journal, hosts, discover=lambda: [candidate()],
                approve=lambda c: True, register=register)
    assert len(attempts) == 1 and calls == [('prepare', 'studio', '10-2-30')]
    assert m.cycle(config(), journal, hosts) == 'QUARANTINED'
    assert len(attempts) == 1


def test_unqualified_hosts_and_unapproved_job_never_register(case):
    journal, hosts, calls = case
    with pytest.raises(ValueError):
        m.cycle({'routing_approved': True, 'qualified_hosts': ['unknown']}, journal, hosts)
    assert m.cycle(config(), journal, hosts, discover=lambda: [candidate()],
                   approve=lambda c: False, register=lambda *a: pytest.fail()) == 'IDLE'
    assert not journal.pending() and not calls


def test_missing_or_tampered_placement_quarantines(case):
    journal, hosts, calls = case
    journal.reserve('10-2-30', 'osvox-prod-' + 'a' * 32)
    with pytest.raises(RuntimeError):
        m.cycle({}, journal, hosts)
    assert journal.pending() and not calls


def test_recovery_error_does_not_select_another_host(case):
    journal, hosts, calls = case
    journal.reserve('10-2-30', 'osvox-prod-' + 'a' * 32)
    m.bind(journal, '10-2-30', 'studio')
    def unavailable(key):
        raise OSError('Studio unreachable')
    hosts['studio'].recover = unavailable
    with pytest.raises(OSError):
        m.cycle(config(), journal, hosts)
    assert not calls
