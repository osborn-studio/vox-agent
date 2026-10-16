import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location('roster_migration', Path(__file__).parents[1] / 'scripts/migrate_sidebar_roster.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def fixture(root):
    (root / 'agents.json').write_text(json.dumps([
        {'id': 'openclaw', 'name': 'Old', 'emoji': 'x', 'agentType': 'system_default', 'isArchived': False, 'bindingStatus': 'bound', 'externalAgentRef': 'keep-me'},
        {'id': 'coding', 'name': 'Code Master', 'emoji': 'x', 'agentType': 'system_default', 'isArchived': False, 'bindingStatus': 'unbound'}, {'id': 'general', 'name': 'General', 'emoji': 'x', 'agentType': 'system_default', 'isArchived': False, 'bindingStatus': 'unbound'}]))
    (root / 'groups.json').write_text('[]')
    (root / 'openclaw-bindings.json').write_text(json.dumps({'openclaw': {'voxlogAgentId': 'openclaw', 'ref': 'keep'}, 'general': {'voxlogAgentId': 'general', 'ref': 'remove'}}))
    with sqlite3.connect(root / 'history.db') as db:
        db.execute('CREATE TABLE recognition_history(id TEXT PRIMARY KEY,agent TEXT,final_text TEXT)')
        db.executemany('INSERT INTO recognition_history VALUES(?,?,?)', [('a','coding','old code'),('b','openclaw','kept'),('c','general','removed'),('d',None,'removed')])


def test_dry_run_and_repeatable_cleanup(tmp_path):
    fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    report = migration.migrate(tmp_path)
    assert sum(n for _, n in report['removed_history_counts']) == 1
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    report = migration.migrate(tmp_path, apply=True)
    snapshot = Path(report['snapshot'])
    assert snapshot.stat().st_mode & 0o077 == 0
    with sqlite3.connect(snapshot / 'history.db') as db:
        assert db.execute('SELECT COUNT(*) FROM recognition_history').fetchone()[0] == 4
    with sqlite3.connect(tmp_path / 'history.db') as db:
        assert db.execute('SELECT id FROM recognition_history ORDER BY id').fetchall() == [('a',), ('b',), ('d',)]
        db.execute("INSERT OR REPLACE INTO recognition_history VALUES('c','general','sync reimport')")
        assert db.execute("SELECT COUNT(*) FROM recognition_history WHERE id='c'").fetchone()[0] == 0
    roster = json.loads((tmp_path / 'agents.json').read_text())
    assert len(roster) == 5
    assert next(a for a in roster if a['id']=='openclaw')['externalAgentRef'] == 'keep-me'
    assert json.loads((tmp_path / 'openclaw-bindings.json').read_text()) == {'openclaw': {'voxlogAgentId': 'openclaw', 'ref': 'keep'}}
    assert migration.migrate(tmp_path, apply=True)['removed_history_counts'] == []


def test_invalid_input_and_symlink_fail_before_writes(tmp_path):
    fixture(tmp_path)
    (tmp_path / 'agents.json').write_text('{}')
    before = (tmp_path / 'history.db').read_bytes()
    with pytest.raises(ValueError):
        migration.migrate(tmp_path, apply=True)
    assert (tmp_path / 'history.db').read_bytes() == before
    (tmp_path / 'agents.json').unlink()
    (tmp_path / 'agents.json').symlink_to(tmp_path / 'groups.json')
    with pytest.raises(ValueError):
        migration.migrate(tmp_path, apply=True)


def test_restore_and_reject_integer_identity(tmp_path):
    fixture(tmp_path)
    original = (tmp_path / 'agents.json').read_bytes()
    report = migration.migrate(tmp_path, apply=True)
    migration.restore(tmp_path, Path(report['snapshot']))
    assert (tmp_path / 'agents.json').read_bytes() == original
    with sqlite3.connect(tmp_path / 'history.db') as db:
        assert db.execute('SELECT COUNT(*) FROM recognition_history').fetchone()[0] == 4
        db.execute('DROP TABLE recognition_history')
        db.execute('CREATE TABLE recognition_history(id INTEGER PRIMARY KEY,agent TEXT)')
    with pytest.raises(ValueError):
        migration.migrate(tmp_path, apply=True)


def test_partial_file_failure_restores_originals(tmp_path, monkeypatch):
    fixture(tmp_path)
    originals = {name: (tmp_path/name).read_bytes() for name in migration.FILES}
    real = migration.atomic
    failed = False
    def fail_once(path, data):
        nonlocal failed
        if path == tmp_path/'groups.json' and not failed:
            failed = True
            raise OSError('injected disk failure')
        return real(path, data)
    monkeypatch.setattr(migration, 'atomic', fail_once)
    with pytest.raises(OSError):
        migration.migrate(tmp_path, apply=True)
    assert originals == {name: (tmp_path/name).read_bytes() for name in migration.FILES}
    with sqlite3.connect(tmp_path/'history.db') as db:
        assert db.execute('SELECT COUNT(*) FROM recognition_history').fetchone()[0] == 4


def test_database_failure_restores_json_and_rows(tmp_path):
    fixture(tmp_path)
    originals = {name: (tmp_path/name).read_bytes() for name in migration.FILES}
    with sqlite3.connect(tmp_path/'history.db') as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("CREATE TRIGGER refuse_delete BEFORE DELETE ON recognition_history BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        migration.migrate(tmp_path, apply=True)
    assert originals == {name: (tmp_path/name).read_bytes() for name in migration.FILES}
    with sqlite3.connect(tmp_path/'history.db') as db:
        assert db.execute('SELECT COUNT(*) FROM recognition_history').fetchone()[0] == 4
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='retired_chat_record'").fetchone()[0] == 0


def test_empty_unassigned_history_is_preserved(tmp_path):
    fixture(tmp_path)
    with sqlite3.connect(tmp_path/'history.db') as db:
        db.execute("INSERT INTO recognition_history VALUES('empty','','dictation')")
    migration.migrate(tmp_path, apply=True)
    with sqlite3.connect(tmp_path/'history.db') as db:
        assert db.execute("SELECT COUNT(*) FROM recognition_history WHERE id='empty'").fetchone()[0] == 1
