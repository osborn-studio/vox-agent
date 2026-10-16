import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/ci'))
spec = importlib.util.spec_from_file_location(
    'macos_vm_launcher', Path(__file__).resolve().parents[2] / 'scripts/ci/macos_vm_launcher.py'
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def case(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    journal = m.VMJournal(tmp_path / 'vms.sqlite')
    calls = []
    host = SimpleNamespace(executable=tmp_path / 'tart', home=tmp_path / 'tart-home',
                           instance_identity=lambda name: '1:2')
    def call(command, timeout=30):
        calls.append(command)
        return '2.337.0' if 'exec' in command else ''
    host._call = call
    monkeypatch.setattr(m, 'validate_tools', lambda h: None)
    monkeypatch.setattr(m, 'verify', lambda *a: {'status': 'PINNED_IMAGE_BYTES_VERIFIED'})
    children = []
    class Process:
        returncode = 0
        def poll(self):
            return None
        def communicate(self, input=None, timeout=None):
            calls.append(('stdin', input, timeout))
            return None, None
        def kill(self):
            calls.append('kill')
        def wait(self, timeout=None):
            return 0
    def popen(command, **kwargs):
        assert 'synthetic-jit' not in str(command)
        assert 'shell' not in kwargs
        assert kwargs['stdout'] == m.subprocess.DEVNULL
        assert kwargs['stdin'] == (m.subprocess.DEVNULL if 'run' in command else m.subprocess.PIPE)
        children.append(command)
        return Process()
    launcher = m.Launcher(host, journal, tmp_path / 'lock', tmp_path / 'manifest', popen=popen)
    yield launcher, journal, calls, children
    journal.close()


def test_durable_clone_binding_and_stdin_only_jit(case):
    launcher, journal, calls, children = case
    assert launcher.launch('10-2-30', 'synthetic-jit')['runner_exited'] is True
    assert journal.pending()[0][3] == 'owned'
    assert len(children) == 2
    boot = children[0]
    for flag in ('--no-graphics', '--no-audio', '--no-clipboard', '--no-usb-accessories', '--net-softnet-block'):
        assert flag in boot
    assert '--dir' not in boot and '--disk' not in boot
    assert ('stdin', b'synthetic-jit', 1500) in calls
    # Launch blocks until the one-job runner exits; recovery owns final cleanup.


def test_verification_failure_preserves_vm_before_any_boot(case, monkeypatch):
    launcher, journal, calls, children = case
    def fail(*args):
        raise ValueError('tampered')
    monkeypatch.setattr(m, 'verify', fail)
    with pytest.raises(ValueError):
        launcher.launch('10-2-30', 'synthetic-jit')
    assert journal.pending() and not children


def test_clone_response_loss_never_adopts_or_replays(case):
    launcher, journal, calls, children = case
    def fail(*a, **kw):
        raise RuntimeError('clone uncertain')
    launcher.host._call = fail
    with pytest.raises(RuntimeError):
        launcher.launch('10-2-30', 'synthetic-jit')
    assert journal.pending()[0][3] == 'clone_unknown'
    with pytest.raises(RuntimeError):
        launcher.launch('10-2-30', 'synthetic-jit')
    with pytest.raises(RuntimeError):
        launcher.launch('10-2-31', 'synthetic-jit')
    assert not children


def test_runner_update_during_job_requires_requalification(case):
    launcher, journal, calls, children = case
    original = launcher.host._call
    def changed(command, **kwargs):
        if '/Users/admin/actions-runner/bin/Runner.Listener' in command:
            return '2.338.0'
        return original(command, **kwargs)
    launcher.host._call = changed
    with pytest.raises(ValueError, match='changed'):
        launcher.launch('10-2-30', 'synthetic-jit')
    assert journal.pending()
