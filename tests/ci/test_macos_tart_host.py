import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "macos_tart_host", Path(__file__).resolve().parents[2] / "scripts/ci/macos_tart_host.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NAME = "osvox-probe-" + "a" * 32


def host(tmp_path, output):
    def run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout=output, stderr='')
    return m.TartHost(tmp_path / 'tart', tmp_path / 'tart-home', run=run)


def test_local_inventory_excludes_cache_without_hiding_invalid_records(tmp_path):
    rows = [{'Source': 'local', 'Name': NAME, 'Running': True},
            {'Source': 'OCI', 'Name': 'ghcr.io/cache', 'Running': False}]
    assert host(tmp_path, json.dumps(rows)).list_vms() == {NAME: True}


@pytest.mark.parametrize('rows', [None, {}, [{'Source': 'local', 'Name': NAME, 'Running': 'true'}],
                                 [{'Source': 'alien', 'Name': NAME, 'Running': False}],
                                 [{'Source': 'local', 'Name': NAME, 'Running': False}] * 2])
def test_unreliable_inventory_never_proves_absence(tmp_path, rows):
    with pytest.raises(ValueError):
        host(tmp_path, json.dumps(rows)).list_vms()


def test_identity_cannot_follow_symlink_or_touch_personal_vm(tmp_path):
    h = host(tmp_path, '')
    (h.home / 'vms').mkdir(parents=True)
    target = tmp_path / 'personal'
    target.mkdir()
    (h.home / 'vms' / NAME).symlink_to(target)
    with pytest.raises(ValueError):
        h.instance_identity(NAME)
    with pytest.raises(ValueError):
        h.delete('personal')


def test_running_or_late_clone_process_blocks_absence(tmp_path):
    prefix = f'{os.getuid()} 123 tart '
    h = host(tmp_path, prefix + '/ci/tart clone base ' + NAME + '\n')
    assert h.processes_absent(NAME) is False
    h = host(tmp_path, prefix + '/ci/tart run ' + NAME + '\n')
    assert h.processes_absent(NAME) is False
    h = host(tmp_path, prefix + '/ci/tart run unrelated\n')
    assert h.processes_absent(NAME) is True


def test_untruncated_ps_and_foreign_or_hidden_tart_args(tmp_path):
    def run(command, **kwargs):
        assert command == ['/bin/ps', '-axww', '-o', 'uid=,pid=,ucomm=,args=']
        return SimpleNamespace(returncode=0, stdout=f'{os.getuid()} 123 tart '
                               + '/long/' * 30 + 'tart clone base ' + NAME, stderr='')
    h = m.TartHost(tmp_path / 'tart', tmp_path / 'home', run=run)
    assert not h.processes_absent(NAME)
    for output in [f'{os.getuid() + 1} 123 tart (tart)', f'{os.getuid()} 123 tart (tart)']:
        with pytest.raises(RuntimeError):
            host(tmp_path, output).processes_absent(NAME)


def test_multiline_argv_continuation_is_scanned_not_misparsed(tmp_path):
    output = f'{os.getuid()} 123 python python script\nmultiline argument {NAME}\n'
    assert not host(tmp_path, output).processes_absent(NAME)
    assert host(tmp_path, f'{os.getuid()} 123 python python script\nother argument\n').processes_absent(NAME)
    with pytest.raises(RuntimeError):
        host(tmp_path, 'not a process row').processes_absent(NAME)


def test_softnet_without_vm_name_still_blocks_capacity_release(tmp_path):
    assert not host(tmp_path, '0 123 softnet (softnet)').processes_absent(NAME)


def test_transport_error_never_claims_process_absence(tmp_path):
    h = m.TartHost(tmp_path / 'tart', tmp_path / 'home', run=lambda *a, **k:
                   SimpleNamespace(returncode=1, stdout='', stderr='sensitive'))
    with pytest.raises(RuntimeError):
        h.processes_absent(NAME)


def test_stop_timeout_preserves_running_vm(tmp_path):
    h = host(tmp_path, '')
    h.list_vms = lambda: {NAME: True}
    with pytest.raises(RuntimeError, match='stop'):
        h.stop(NAME, timeout=0)


def test_exact_instance_identity(tmp_path):
    h = host(tmp_path, '')
    directory = h.home / 'vms' / NAME
    directory.mkdir(parents=True)
    st = directory.stat()
    assert h.instance_identity(NAME) == f'{st.st_dev}:{st.st_ino}'


def test_workspace_absence_does_not_ignore_dangling_symlink(tmp_path):
    h = host(tmp_path, '')
    (h.home / 'vms').mkdir(parents=True)
    assert h.workspace_absent(NAME)
    (h.home / 'vms' / NAME).symlink_to(tmp_path / 'missing')
    assert not h.workspace_absent(NAME)
