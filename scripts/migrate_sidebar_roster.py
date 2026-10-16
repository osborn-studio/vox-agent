#!/usr/bin/env python3
"""Local roster cleanup; dry-run default. Quit Vox before --apply or --restore.
Snapshots are private. No cloud, credentials, outbox or external sync repo access.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile

KEEP = ('osborn_pa', 'ostag', 'hermes', 'openclaw', 'osbornalpha', 'coding')
FILES = ('agents.json', 'groups.json', 'openclaw-bindings.json')
SEEDS = [('osborn_pa', 'osbornpa', '🌊'), ('ostag', 'ostag', '🏷️'),
         ('hermes', 'hermes', '🪽'), ('openclaw', 'openclaw', '🦞'),
         ('osbornalpha', 'osbornalpha', 'α')]


def atomic(path, data):
    fd, name = tempfile.mkstemp(prefix='.roster-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_paths(root):
    for name in (*FILES, 'history.db', 'RosterMigrationBackups'):
        if (root / name).is_symlink():
            raise ValueError('Refusing symlinked application data')


def restore(root, snapshot):
    root = root.resolve(strict=True)
    validate_paths(root)
    snapshot = snapshot.resolve(strict=True)
    if snapshot.parent != root / 'RosterMigrationBackups':
        raise ValueError('Snapshot must belong to this local data directory')
    manifest = json.loads((snapshot / 'audit.json').read_text())
    for name in FILES:
        if name in manifest['original_files']:
            atomic(root / name, (snapshot / name).read_bytes())
        else:
            (root / name).unlink(missing_ok=True)
    if manifest['had_database']:
        with sqlite3.connect(snapshot / 'history.db') as src, sqlite3.connect(root / 'history.db') as dst:
            src.backup(dst)
    return {'restored': str(snapshot), 'warning': 'Snapshot restore replaces local changes made since cleanup'}


def migrate(root: Path, apply=False):
    root = root.resolve(strict=True)
    validate_paths(root)
    originals = {name: (root / name).read_bytes() for name in FILES if (root / name).exists()}
    agents = json.loads(originals.get('agents.json', b'[]'))
    if not isinstance(agents, list) or any(not isinstance(a, dict) or not isinstance(a.get('id'), str) for a in agents):
        raise ValueError('Invalid agent list')
    for a in agents:
        if (any(not isinstance(a.get(k), str) for k in ('name', 'emoji'))
                or a.get('agentType') not in ('system_default', 'user_created')
                or a.get('bindingStatus') not in ('bound', 'unbound')
                or type(a.get('isArchived')) is not bool):
            raise ValueError('Agent does not match Swift Codable schema')
        for k in ('parentId', 'externalSystem', 'externalAgentRef'):
            if a.get(k) is not None and not isinstance(a[k], str):
                raise ValueError('Invalid optional agent field')
    bindings = json.loads(originals.get('openclaw-bindings.json', b'{}'))
    if not isinstance(bindings, dict) or any(not isinstance(v, dict) or v.get('voxlogAgentId') != k for k, v in bindings.items()):
        raise ValueError('Invalid bindings schema')
    groups = json.loads(originals.get('groups.json', b'[]'))
    if not isinstance(groups, list) or any(not isinstance(g, dict) or not isinstance(g.get('id'), str) for g in groups):
        raise ValueError('Invalid groups schema')
    roster = []
    for agent_id, name, emoji in SEEDS:
        a = next((dict(a) for a in agents if a['id'] == agent_id),
                 dict(id=agent_id, emoji=emoji, agentType='system_default', bindingStatus='unbound'))
        a.update(name=name, isArchived=False)
        a.pop('parentId', None)
        roster.append(a)
    dbpath = root / 'history.db'
    mode = 'rw' if apply else 'ro'
    db = sqlite3.connect(f'{dbpath.as_uri()}?mode={mode}', uri=True) if dbpath.exists() else None
    try:
        counts = []
        unassigned = 0
        marks = ','.join('?' for _ in KEEP)
        where = f"agent IS NOT NULL AND agent != '' AND agent NOT IN ({marks})"
        if db:
            columns = {row[1]: row for row in db.execute('PRAGMA table_info(recognition_history)')}
            if 'id' not in columns or columns['id'][2].upper() != 'TEXT' or not columns['id'][5]:
                raise ValueError('Expected sync-stable TEXT primary key')
            counts = db.execute(f'SELECT agent,COUNT(*) FROM recognition_history WHERE {where} GROUP BY agent', KEEP).fetchall()
            unassigned = db.execute('SELECT COUNT(*) FROM recognition_history WHERE agent IS NULL OR agent = \'\'').fetchone()[0]
        report = dict(removed_agents=[a['id'] for a in agents if a['id'] not in KEEP],
                      removed_bindings=[k for k in bindings if k not in KEEP or k == 'coding'],
                      removed_groups=[g['id'] for g in groups],
                      removed_history_counts=counts, unassigned_history_preserved=unassigned,
                      retained_history_agents=list(KEEP), original_files=list(originals),
                      had_database=db is not None, applied=False)
        if not apply:
            return report
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        backup = root / 'RosterMigrationBackups' / stamp
        backup.mkdir(parents=True, mode=0o700)
        os.chmod(backup.parent, 0o700)
        for name, data in originals.items():
            atomic(backup / name, data)
        if db:
            fd = os.open(backup / 'history.db', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with sqlite3.connect(backup / 'history.db') as snapshot:
                db.backup(snapshot)
                if snapshot.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Snapshot integrity failed')
        atomic(backup / 'audit.json', json.dumps(report, indent=2).encode())
        try:
            if db:
                db.execute('BEGIN EXCLUSIVE')
            for name, value in [('agents.json', roster), ('groups.json', []),
                                ('openclaw-bindings.json', {k: v for k, v in bindings.items() if k in KEEP and k != 'coding'})]:
                atomic(root / name, json.dumps(value, ensure_ascii=False, indent=2).encode())
            if db:
                db.execute('CREATE TABLE IF NOT EXISTS retired_chat_record (id TEXT PRIMARY KEY)')
                db.execute("""CREATE TRIGGER IF NOT EXISTS ignore_retired_chat_insert
                    BEFORE INSERT ON recognition_history
                    WHEN EXISTS (SELECT 1 FROM retired_chat_record WHERE id=NEW.id)
                    BEGIN SELECT RAISE(IGNORE); END""")
                db.execute(f'INSERT OR IGNORE INTO retired_chat_record SELECT id FROM recognition_history WHERE {where}', KEEP)
                db.execute(f'DELETE FROM recognition_history WHERE {where}', KEEP)
                db.commit()
        except Exception:
            if db:
                db.rollback()
            # Restore all original JSON if any file write or database operation fails.
            for name in FILES:
                if name in originals:
                    atomic(root / name, originals[name])
                else:
                    (root / name).unlink(missing_ok=True)
            raise
        report.update(applied=True, snapshot=str(backup))
        atomic(backup / 'audit.json', json.dumps(report, indent=2).encode())
        return report
    finally:
        if db:
            db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'Library/Application Support/VoxLog')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true')
    action.add_argument('--restore', type=Path)
    args = parser.parse_args()
    if args.apply or args.restore:
        import subprocess
        # Shipped CFBundleExecutable is VoxLog, independently of display name.
        if subprocess.run(['pgrep', '-x', 'VoxLog'], capture_output=True).returncode == 0:
            parser.error('Quit Vox Agent before applying/restoring; dry-run remains available')
    print(json.dumps(restore(args.root, args.restore) if args.restore else migrate(args.root, args.apply), ensure_ascii=False, indent=2))
