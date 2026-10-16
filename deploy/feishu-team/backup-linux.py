#!/usr/bin/env python3
"""Snapshot stopped-service SQLite databases, encrypt, verify, retain seven copies."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone


def digest(stream):
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        result.update(chunk)
    return result.digest()


def recover(runtime):
    """Only undo a stop initiated by this backup, including timeout recovery."""
    marker = runtime / 'stopped-by-backup'
    if marker.exists():
        subprocess.run(['systemctl', 'start', 'vox-team'], check=True)
        marker.unlink()



def cleanup_staging(destination):
    """Only our abandoned artifacts; caller must own the exclusive backup unit."""
    for path in destination.glob('.work-*'):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
    for path in destination.glob('vox-????????T??????Z.tar.gpg.partial'):
        if path.is_file() and not path.is_symlink():
            path.unlink()


def terminate(signum, frame):
    raise SystemExit(128 + signum)

def backup(root, destination, runtime, config, password):
    os.umask(0o077)
    if not root.is_dir():
        raise RuntimeError('Missing database directory; snapshot aborted')
    destination.mkdir(mode=0o700, exist_ok=True)
    runtime.mkdir(mode=0o700, exist_ok=True)
    if (runtime / 'stopped-by-backup').exists():
        raise RuntimeError('Unresolved backup stop marker; explicit recovery required')
    cleanup_staging(destination)
    size = sum(p.stat().st_size for p in root.rglob('*')
               if p.is_file() and not p.is_symlink())
    if shutil.disk_usage(destination).free < 3 * size + 1024**3:
        raise RuntimeError('Insufficient backup headroom')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = destination / ('vox-' + stamp + '.tar.gpg')
    if output.exists():
        raise RuntimeError('Backup timestamp already exists')
    partial = destination / (output.name + '.partial')
    try:
        # Disk staging avoids charging entire snapshots to the 256 MB tmpfs limit.
        with tempfile.TemporaryDirectory(dir=destination, prefix='.work-') as temp:
            temp = Path(temp)
            (temp / 'gnupg').mkdir(mode=0o700)
            gpg_env = dict(os.environ, GNUPGHOME=str(temp / 'gnupg'))
            snapshot = temp / 'snapshot'
            snapshot.mkdir()
            state = subprocess.run(
                ['systemctl', 'show', '-P', 'ActiveState', 'vox-team'],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            if state not in ('active', 'inactive', 'failed'):
                raise RuntimeError('Service is transitioning; snapshot aborted')
            active = state == 'active'
            try:
                if active:
                    (runtime / 'stopped-by-backup').touch(mode=0o600)
                    subprocess.run(['systemctl', 'stop', 'vox-team'], check=True)
                    stopped = subprocess.run(
                        ['systemctl', 'show', '-P', 'ActiveState', 'vox-team'],
                        check=True, capture_output=True, text=True,
                    ).stdout.strip()
                    if stopped not in ('inactive', 'failed'):
                        raise RuntimeError('Service did not stop; snapshot aborted')
                count = 0
                inventory = {}
                for source in root.rglob('*'):
                    if source.is_symlink():
                        raise RuntimeError('Unexpected symlink; snapshot aborted')
                    if source.is_dir():
                        if source.name.endswith('.worker.lock'):
                            raise RuntimeError('Unexpected lock directory; snapshot aborted')
                        continue
                    if not source.is_file():
                        raise RuntimeError('Unexpected special file; snapshot aborted')
                    # WorkerRuntime creates this empty advisory-lock file beside
                    # its database. Never unlink it: replacing a lock inode can
                    # allow competing workers. Unknown files still fail closed.
                    if source == root / 'input-backup.sqlite.worker.lock':
                        if source.stat().st_size != 0 or not (root / 'input-backup.sqlite').is_file():
                            raise RuntimeError('Unexpected worker lock; snapshot aborted')
                        continue
                    if source.name.endswith(('-wal', '-shm')):
                        database = source.with_name(source.name[:-4])
                        if database.is_symlink() or not database.is_file():
                            raise RuntimeError('Unexpected orphan SQLite sidecar; snapshot aborted')
                        with database.open('rb') as stream:
                            if stream.read(16) != b'SQLite format 3\x00':
                                raise RuntimeError('Unexpected sidecar database; snapshot aborted')
                        continue
                    with source.open('rb') as stream:
                        if stream.read(16) != b'SQLite format 3\x00':
                            raise RuntimeError('Unexpected data file; snapshot aborted')
                    target = snapshot / source.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
                        src.backup(dst)
                        dst.execute('PRAGMA journal_mode=DELETE')
                        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise RuntimeError('SQLite integrity check failed')
                    with target.open('rb') as stream:
                        inventory[target.relative_to(snapshot).as_posix()] = {
                            'size': target.stat().st_size, 'sha256': digest(stream).hex(),
                        }
                    count += 1
                if not count:
                    raise RuntimeError('No SQLite databases; snapshot aborted')
                shutil.copyfile(config, snapshot / 'runtime.json')
                config_copy = snapshot / 'runtime.json'
                with config_copy.open('rb') as stream:
                    inventory['runtime.json'] = {
                        'size': config_copy.stat().st_size, 'sha256': digest(stream).hex(),
                    }
                (snapshot / 'manifest.json').write_text(json.dumps({
                    'schemaVersion': 2,
                    'files': inventory,
                    'createdAt': stamp,
                    'release': str(Path('/opt/vox-agent/current').resolve()),
                    'databases': count,
                }))
            finally:
                if active:
                    recover(runtime)
            archive = temp / 'snapshot.tar'
            with tarfile.open(archive, 'w') as tar:
                tar.add(snapshot, arcname='snapshot')
            gpg = ['gpg', '--batch', '--yes', '--no-symkey-cache', '--pinentry-mode', 'loopback',
                   '--passphrase-file', str(password)]
            subprocess.run(gpg + ['--symmetric', '--cipher-algo', 'AES256',
                                 '--output', str(partial), str(archive)],
                           check=True, env=gpg_env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Stream decrypted verification instead of holding a third full copy.
            with archive.open('rb') as stream:
                expected = digest(stream)
            with subprocess.Popen(gpg + ['--decrypt', str(partial)], env=gpg_env,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                actual = digest(process.stdout)
                if process.wait() != 0 or actual != expected:
                    raise RuntimeError('Backup restore verification failed')
            with partial.open('rb') as stream:
                os.fsync(stream.fileno())
            partial.rename(output)
            directory = os.open(destination, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        partial.unlink(missing_ok=True)
    for old in sorted(destination.glob('vox-????????T??????Z.tar.gpg'))[:-7]:
        old.unlink()
    return output


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, terminate)
    runtime = Path('/run/vox-team-backup')
    if sys.argv[1:] == ['--recover']:
        try:
            recover(runtime)
        finally:
            cleanup_staging(Path('/var/lib/vox-team-backups'))
    elif not sys.argv[1:]:
        backup(Path('/var/lib/vox-team/data'), Path('/var/lib/vox-team-backups'),
               runtime, Path('/etc/vox-team/runtime.json'),
               Path(os.environ['CREDENTIALS_DIRECTORY']) / 'backup-password')
        print('Encrypted SQLite backup and decrypt verification passed')
    else:
        raise SystemExit('Unsupported arguments')
