"""Exercise backup lifecycle with real SQLite/GPG and a fake service manager."""
import importlib.util
import io
import json
import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import tarfile
from pathlib import Path

import pytest


def load_module():
    spec = importlib.util.spec_from_file_location(
        'linux_backup', Path(__file__).resolve().parents[1] / 'deploy/feishu-team/backup-linux.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup(monkeypatch):
    # GPG agent Unix socket paths must fit macOS's 104-byte limit.
    short_temp = tempfile.TemporaryDirectory(prefix="vx-bk-", dir="/tmp")
    tmp_path = Path(short_temp.name)
    previous_umask = os.umask(0o077)
    module = load_module()
    data = tmp_path / 'data'
    data.mkdir()
    dest = tmp_path / 'backups'
    dest.mkdir()
    runtime = tmp_path / 'run'
    runtime.mkdir()
    config = tmp_path / 'runtime.json'
    config.write_text('{}')
    password = tmp_path / 'password'
    password.write_text('synthetic-test-only')
    calls = []
    original = subprocess.run

    def run(args, **kwargs):
        if args[0] == 'systemctl':
            calls.append(args[1])
            return subprocess.CompletedProcess(args, 0, stdout=('inactive\n' if 'stop' in calls else 'active\n'))
        return original(args, **kwargs)

    monkeypatch.setattr(module.subprocess, 'run', run)
    try:
        yield module, data, dest, runtime, config, password, calls
    finally:
        short_temp.cleanup()
        os.umask(previous_umask)


def test_missing_or_empty_data_never_rotates_existing_backups(setup):
    module, data, dest, runtime, config, password, calls = setup
    old = dest / 'vox-20260101T000000Z.tar.gpg'
    old.write_bytes(b'old encrypted archive')
    for root in (data, data / 'missing'):
        with pytest.raises(RuntimeError, match='database|directory'):
            module.backup(root, dest, runtime, config, password)
    assert old.read_bytes() == b'old encrypted archive'
    assert not list(dest.glob('*.partial'))


def test_recovery_does_not_start_intentionally_stopped_service(setup):
    module, _, _, runtime, _, _, calls = setup
    module.recover(runtime)
    assert calls == []
    (runtime / 'stopped-by-backup').touch()
    module.recover(runtime)
    assert calls == ['start']
    assert not (runtime / 'stopped-by-backup').exists()


def test_real_encrypted_snapshot_and_restart_on_disk(setup, monkeypatch):
    if not shutil.which('gpg'):
        pytest.skip('GPG not installed')
    module, data, dest, runtime, config, password, calls = setup
    with sqlite3.connect(data / 'sessions.db') as connection:
        connection.execute('create table records(value text)')
        connection.execute("insert into records values ('synthetic')")
    with sqlite3.connect(data / 'input-backup.sqlite') as connection:
        connection.execute('create table events(value text)')
    lock = data / 'input-backup.sqlite.worker.lock'
    lock.touch()
    lock_inode = lock.stat().st_ino
    for index in range(1, 9):
        (dest / f'vox-202601{index:02d}T000000Z.tar.gpg').write_bytes(b'old archive')
    original_temp = module.tempfile.TemporaryDirectory
    staging = []

    def tempdir(**kwargs):
        staging.append(Path(kwargs['dir']))
        return original_temp(**kwargs)

    monkeypatch.setattr(module.tempfile, 'TemporaryDirectory', tempdir)
    archive = module.backup(data, dest, runtime, config, password)
    assert staging == [dest]
    assert calls == ['show', 'stop', 'show', 'start']
    assert archive.exists()
    assert lock.stat().st_ino == lock_inode
    assert archive.read_bytes()[:6] != b'SQLite'
    assert not list(dest.glob('.work-*'))
    assert not list(dest.glob('*.partial'))
    assert not (runtime / 'stopped-by-backup').exists()
    assert len(list(dest.glob('vox-*.tar.gpg'))) == 7
    gnupg = runtime / 'verify-gpg'
    gnupg.mkdir(mode=0o700)
    result = subprocess.run(
        ['gpg', '--batch', '--yes', '--no-symkey-cache', '--pinentry-mode', 'loopback',
         '--passphrase-file', str(password), '--decrypt', str(archive)],
        check=True, capture_output=True, env=dict(os.environ, GNUPGHOME=str(gnupg)),
    )
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as bundle:
        assert not any(name.endswith('.worker.lock') for name in bundle.getnames())
        assert bundle.extractfile('snapshot/runtime.json').read() == b'{}'
        restored = runtime / 'restored.db'
        restored.write_bytes(bundle.extractfile('snapshot/sessions.db').read())
        manifest = json.load(bundle.extractfile('snapshot/manifest.json'))
        assert manifest['schemaVersion'] == 2
        assert set(manifest['files']) == {'sessions.db', 'input-backup.sqlite', 'runtime.json'}
        assert manifest['files']['sessions.db']['sha256'] == hashlib.sha256(restored.read_bytes()).hexdigest()
    verifier_spec = importlib.util.spec_from_file_location(
        'restore_verifier', Path(__file__).parents[1] / 'deploy/feishu-team/verify-restore.py')
    verifier = importlib.util.module_from_spec(verifier_spec)
    verifier_spec.loader.exec_module(verifier)
    tar_path = runtime / 'verify.tar'
    tar_path.write_bytes(result.stdout)
    assert verifier.verify(tar_path, runtime)['manifest_verified'] is True
    with sqlite3.connect(restored) as connection:
        assert connection.execute('select value from records').fetchall() == [('synthetic',)]


def test_snapshot_error_restores_service_without_publishing(setup):
    module, data, dest, runtime, config, password, calls = setup
    (data / 'invalid.db').write_bytes(b'invalid')
    with pytest.raises(RuntimeError, match='Unexpected'):
        module.backup(data, dest, runtime, config, password)
    assert calls == ['show', 'stop', 'show', 'start']
    assert not list(dest.glob('*.gpg'))


def test_failed_restart_keeps_marker_for_stop_post(setup, monkeypatch):
    module, _, _, runtime, _, _, _ = setup
    marker = runtime / 'stopped-by-backup'
    marker.touch()

    def failed(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(module.subprocess, 'run', failed)
    with pytest.raises(subprocess.CalledProcessError):
        module.recover(runtime)
    assert marker.exists()


def test_encryption_failure_removes_partial_and_preserves_old_backups(setup, monkeypatch):
    module, data, dest, runtime, config, password, calls = setup
    with sqlite3.connect(data / 'sessions.db') as connection:
        connection.execute('create table records(value text)')
    old = dest / 'vox-20260101T000000Z.tar.gpg'
    old.write_bytes(b'old encrypted archive')
    previous = module.subprocess.run

    def failed(args, **kwargs):
        if args[0] == 'gpg':
            Path(args[args.index('--output') + 1]).write_bytes(b'incomplete')
            raise subprocess.CalledProcessError(1, args)
        return previous(args, **kwargs)

    monkeypatch.setattr(module.subprocess, 'run', failed)
    with pytest.raises(subprocess.CalledProcessError):
        module.backup(data, dest, runtime, config, password)
    assert calls == ['show', 'stop', 'show', 'start']
    assert old.read_bytes() == b'old encrypted archive'
    assert not list(dest.glob('*.partial'))


def test_stopped_service_stays_stopped_on_snapshot_failure(setup, monkeypatch):
    module, data, dest, runtime, config, password, calls = setup

    def inactive(args, **kwargs):
        calls.append(args[1])
        return subprocess.CompletedProcess(args, 0, stdout='inactive\n')

    monkeypatch.setattr(module.subprocess, 'run', inactive)
    with pytest.raises(RuntimeError, match='No SQLite'):
        module.backup(data, dest, runtime, config, password)
    assert calls == ['show']
    assert not (runtime / 'stopped-by-backup').exists()


def test_headroom_checked_on_backup_volume_before_stopping(setup, monkeypatch):
    module, data, dest, runtime, config, password, calls = setup
    checked = []

    def full(path):
        checked.append(path)
        return type('Usage', (), {'free': 0})()

    monkeypatch.setattr(module.shutil, 'disk_usage', full)
    with pytest.raises(RuntimeError, match='headroom'):
        module.backup(data, dest, runtime, config, password)
    assert checked == [dest]
    assert not calls


def test_abandoned_plaintext_and_partial_cleaned_only_in_owned_namespace(setup):
    module, _, dest, _, _, _, _ = setup
    abandoned = dest / '.work-dead'
    abandoned.mkdir()
    (abandoned / 'snapshot.tar').write_bytes(b'synthetic plaintext')
    partial = dest / 'vox-20260101T000000Z.tar.gpg.partial'
    partial.write_bytes(b'incomplete')
    untouched = dest / 'operator-note'
    untouched.write_text('preserve')
    module.cleanup_staging(dest)
    assert not abandoned.exists()
    assert not partial.exists()
    assert untouched.read_text() == 'preserve'


def test_transitioning_service_aborts_without_stop_or_restart(setup, monkeypatch):
    module, data, dest, runtime, config, password, calls = setup

    def activating(args, **kwargs):
        calls.append(args[1])
        return subprocess.CompletedProcess(args, 0, stdout='activating\n')

    monkeypatch.setattr(module.subprocess, 'run', activating)
    with pytest.raises(RuntimeError, match='transitioning'):
        module.backup(data, dest, runtime, config, password)
    assert calls == ['show']
    assert not list(dest.glob('.work-*'))


@pytest.mark.parametrize('name,content', [
    ('other.sqlite.worker.lock', b''),
    ('input-backup.sqlite.worker.lock', b'not a lock'),
    ('nested/input-backup.sqlite.worker.lock', b''),
])
def test_only_exact_empty_worker_lock_is_allowed(setup, name, content):
    module, data, dest, runtime, config, password, calls = setup
    with sqlite3.connect(data / 'input-backup.sqlite') as connection:
        connection.execute('create table events(value text)')
    lock = data / name
    lock.parent.mkdir(exist_ok=True)
    lock.write_bytes(content)
    with pytest.raises(RuntimeError, match='Unexpected'):
        module.backup(data, dest, runtime, config, password)
    assert lock.exists()
    assert not list(dest.glob('*.gpg'))
    assert calls[-1] == 'start'


def test_stop_success_without_inactive_state_aborts(setup, monkeypatch):
    module, data, dest, runtime, config, password, calls = setup
    def still_active(args, **kwargs):
        calls.append(args[1])
        return subprocess.CompletedProcess(args, 0, stdout='active\n')
    monkeypatch.setattr(module.subprocess, 'run', still_active)
    with pytest.raises(RuntimeError, match='did not stop'):
        module.backup(data, dest, runtime, config, password)
    assert calls == ['show', 'stop', 'show', 'start']
    assert not list(dest.glob('*.gpg'))


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'orphan-wal', 'lock-directory', 'lock-without-db'])
def test_unexpected_files_fail_closed(setup, kind):
    module, data, dest, runtime, config, password, calls = setup
    if kind == 'symlink':
        (data/'linked.db').symlink_to(config)
    elif kind == 'fifo':
        os.mkfifo(data/'pipe')
    elif kind == 'orphan-wal':
        (data/'missing.db-wal').touch()
    elif kind == 'lock-directory':
        (data/'input-backup.sqlite.worker.lock').mkdir()
    else:
        (data/'input-backup.sqlite.worker.lock').touch()
    with pytest.raises(RuntimeError, match='Unexpected'):
        module.backup(data, dest, runtime, config, password)
    assert not list(dest.glob('*.gpg'))
    assert calls[-1] == 'start'


def test_unresolved_marker_requires_explicit_recovery(setup):
    module, data, dest, runtime, config, password, calls = setup
    marker = runtime/'stopped-by-backup'
    marker.touch()
    with pytest.raises(RuntimeError, match='explicit recovery'):
        module.backup(data, dest, runtime, config, password)
    assert calls == []
    assert marker.exists()
