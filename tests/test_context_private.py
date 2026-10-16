"""Personal journal/revision gate: isolated synthetic DBs, no tenant or model calls."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient
import pytest

from engine.backup_app import create_backup_app
from engine.context_projects import ContextProject
from engine.org_models import Organization
from engine.context_private import PrivateContextJournal
from tests.test_feishu_backup import setup as setup


@pytest.fixture
def private(setup):
    _, store, headers, target, context = setup
    context = replace(context, private_context_enabled=True)
    with TestClient(create_backup_app(context)) as client:
        yield client, store, headers, target, context


def create(client, headers, **changes):
    return client.post('/v1/context-private', headers=headers,
                       json={'eventId': 'personal-1', 'text': '需要修复 parser', **changes})


def window(**changes):
    now = datetime.now(timezone.utc)
    return {'start': (now-timedelta(days=1)).isoformat(), 'end': (now+timedelta(days=1)).isoformat(), **changes}


def listing(client, headers, **changes):
    return client.get('/v1/context-private', headers=headers, params=window(**changes))


def check(client, headers, identifier, revision):
    return client.post('/v1/context-private-reference-check', headers=headers,
                       json={'sources': [{'id': identifier, 'revision': revision}]})


def other_headers(context, role='member', org='default-org'):
    auth = context.auth_service
    if auth.org_store.get_organization(org) is None:
        auth.org_store.insert_organization(Organization(id=org, name="Synthetic"))
    user = auth.create_user(org_id=org, email=f'{role}-{org}@example.test', display_name='Other')
    auth.org_store.update_user(user['id'], role=role)
    _, token = auth.issue_credential(user_id=user['id'], name='synthetic')
    return {'Authorization': f'Bearer {token}'}


def test_private_default_disabled_has_no_schema_or_routes(setup):
    client, store, headers, _, _ = setup
    assert create(client, headers).status_code == 404
    assert client.get('/v1/context-private-events/personal-1', headers=headers).status_code == 404
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='private_context_v1'").fetchone()


def test_owner_create_read_no_delivery(private):
    client, store, headers, _, context = private
    context.enabled = False  # Delivery paused does not gate personal journal.
    result = create(client, headers, tags=['question'])
    assert result.status_code == 200
    record = result.json()
    assert record['visibility'] == 'private' and record['evidenceKind'] == 'user-statement'
    assert record['revision'] == 1 and record['projectId'] is None
    assert store.count() == 0 and store.db.execute('SELECT count(*) FROM backup_outbox').fetchone()[0] == 0
    assert listing(client, headers).json()['items'] == [record]
    assert result.headers['cache-control'] == 'no-store'
    assert result.headers['referrer-policy'] == 'no-referrer'
    assert result.headers['x-content-type-options'] == 'nosniff'
    assert client.get('/v1/context-private/'+record['id'], headers=headers).json() == record
    assert check(client, headers, record['id'], 1).json()['valid'] is True
    assert create(client, headers).status_code == 409  # Different tags cannot replay.
    assert create(client, headers, tags=['question']).json() == record
    assert create(client, {}).status_code == 401


@pytest.mark.parametrize('role,org', [('member','default-org'),('admin','default-org'),('admin','other-org')])
def test_no_actor_or_admin_widening(private, role, org):
    client, _, headers, _, context = private
    own = create(client, headers).json()
    other = other_headers(context, role=role, org=org)
    assert listing(client, other).json()['items'] == []
    assert client.get('/v1/context-private/'+own['id'], headers=other).status_code == 404
    assert client.put('/v1/context-private/'+own['id'], headers=other,
                      json={'expectedRevision':1,'text':'overwrite'}).status_code == 404
    assert client.post('/v1/context-private/'+own['id']+'/withdraw', headers=other,
                       json={'expectedRevision':1}).status_code == 404
    assert check(client, other, own['id'], 1).json()['valid'] is False
    assert create(client, other).status_code == 200  # Same event name has distinct owner namespace.
    assert listing(client, headers).json()['items'] == [own]


def test_edit_withdraw_preserve_history_and_invalidate_references(private):
    client, store, headers, _, _ = private
    own = create(client, headers).json()
    identifier = own['id']
    route = '/v1/context-private/'+identifier
    edit = {'expectedRevision':1,'text':'修复了 parser；尚未验证','tags':['progress']}
    changed = client.put(route, headers=headers, json=edit).json()
    assert changed['revision'] == 2 and changed['text'] == edit['text']
    assert check(client, headers, identifier, 1).json()['valid'] is False
    assert check(client, headers, identifier, 2).json()['valid'] is True
    assert client.put(route, headers=headers, json=edit).status_code == 409
    assert client.post(route+'/withdraw', headers=headers, json={'expectedRevision':1}).status_code == 409
    withdrawn = client.post(route+'/withdraw', headers=headers, json={'expectedRevision':2}).json()
    assert withdrawn['revision'] == 3 and withdrawn['withdrawn']
    assert 'text' not in withdrawn and 'tags' not in withdrawn
    assert client.post(route+'/withdraw', headers=headers, json={'expectedRevision':2}).json() == withdrawn
    assert client.get(route, headers=headers).json() == withdrawn
    assert check(client, headers, identifier, 2).json()['valid'] is False
    assert listing(client, headers).json()['items'] == []
    assert client.put(route, headers=headers, json={'expectedRevision':3,'text':'restore'}).status_code == 409
    assert create(client, headers).json() == withdrawn  # Retry never resurrects withdrawn text.
    history = store.db.execute('SELECT text FROM private_context_revisions_v1 WHERE id=? ORDER BY revision', (identifier,)).fetchall()
    assert [row['text'] for row in history] == ['需要修复 parser',edit['text'],'']
    assert store.count() == 0


def test_project_snapshot_current_access_and_reference_revocation(private):
    client, _, headers, target, context = private
    actor = next(iter(target.allowed_users))
    context.projects = (ContextProject(id='vox', org_id=target.org_id, name='Vox', repository_id='team/repo', allowed_users=(actor,)),)
    own = create(client, headers, projectId='vox').json()
    assert own['repositoryId'] == 'team/repo'
    context.projects = (context.projects[0].model_copy(update={'repository_id':'team/renamed'}),)
    assert client.get('/v1/context-private/'+own['id'], headers=headers).json()['repositoryId'] == 'team/repo'
    context.projects = ()
    assert listing(client, headers).json()['items'] == []
    assert listing(client, headers).json()['omittedRecords'] is True
    assert check(client, headers, own['id'], 1).json()['valid'] is False
    assert create(client, headers, projectId='vox').status_code == 404
    assert create(client, headers, eventId='new',projectId='vox').status_code == 403
    assert client.put('/v1/context-private/'+own['id'], headers=headers,
                      json={'expectedRevision':1,'text':'changed'}).status_code == 404
    terminal = client.post('/v1/context-private/'+own['id']+'/withdraw',headers=headers,
                           json={'expectedRevision':1}).json()
    assert terminal['withdrawn'] and 'text' not in terminal


def test_search_tags_window_cursor_parameterized(private):
    client, _, headers, _, _ = private
    first = create(client, headers, tags=['blocker']).json()
    second = create(client, headers, eventId='personal-2',text='完成 UI',tags=['progress']).json()
    assert client.post("/v1/context-private-search", headers=headers, json=window(q="parser", tag="blocker")).json()["items"] == [first]
    assert client.post("/v1/context-private-search", headers=headers, json=window(q="' OR 1=1 --")).json()["items"] == []
    assert listing(client, headers, q="private text must not be queried via URL").status_code == 422
    page = listing(client, headers, limit='1').json()
    assert page['items'] == [second] and page['hasMore']
    last = listing(client, headers, limit='1',cursor=page['nextCursor']).json()
    assert last['items'] == [first] and not last['hasMore']
    assert listing(client, headers, actorId='other').status_code == 422
    assert listing(client, headers, tag='verified-complete').status_code == 422
    assert listing(client, headers, limit='true').status_code == 422


@pytest.mark.parametrize('body', [
    {'visibility':'team'}, {'actorId':'other'}, {'evidenceKind':'verified-code'},
    {'tags':['progress','progress']}, {'tags':['completed']}, {'text':''}, {'text':'a'*8193},
])
def test_invalid_private_payload_no_sensitive_error(private, body):
    client, _, headers, _, _ = private
    result = create(client, headers, **body)
    assert result.status_code == 422
    assert 'parser' not in result.text and 'progress' not in result.text


def test_strict_mutations_and_bounded_body(private):
    client, _, headers, _, _ = private
    own = create(client, headers).json()
    assert client.put('/v1/context-private/'+own['id'], headers=headers,
                      json={'expectedRevision':True,'text':'private'}).status_code == 422
    assert client.post('/v1/context-private', headers=headers,
                       content='{"eventId":"a","text":"secret","text":"duplicate"}').status_code == 422
    assert client.post('/v1/context-private', headers=headers,content='x'*131073).status_code == 413
    assert client.post('/v1/context-private-reference-check', headers=headers,json={'sources':[]}).status_code == 422
    assert client.post('/v1/context-private-reference-check', headers=headers,
                       json={'sources':[{'id':own['id'],'revision':1}]*2}).status_code == 422


def test_reopen_journal_additive_migration(private):
    client, store, headers, _, context = private
    own = create(client, headers).json()
    context.private_context_store = PrivateContextJournal(store.path)
    assert client.get('/v1/context-private/'+own['id'], headers=headers).json() == own
    assert store.count() == 0


def test_member_suspended_fails_closed(private):
    client, _, headers, target, context = private
    own = create(client, headers).json()
    context.auth_service.org_store.update_user(next(iter(target.allowed_users)), status='suspended')
    result = check(client, headers, own['id'], 1)
    assert result.status_code in (401,403)
    assert '需要' not in result.text


def test_corrupt_tags_and_text_fail_closed(private):
    client, store, headers, _, _ = private
    own = create(client, headers).json()
    store.db.execute("DROP TRIGGER private_revision_no_update_v1")  # Synthetic corruption fixture only.
    store.db.execute('UPDATE private_context_revisions_v1 SET tags=? WHERE id=?', (json.dumps(['unexpected']), own['id']))
    store.db.commit()
    result = listing(client, headers)
    assert result.status_code == 503 and 'parser' not in result.text


@pytest.mark.parametrize('change', ['suspend','feature','credential'])
def test_authority_rechecked_after_body_await(private, monkeypatch, change):
    from engine import context_private

    client, store, headers, target, context = private
    original = context_private._body

    async def altered(request, model):
        value = await original(request, model)
        if change == 'suspend':
            context.auth_service.org_store.update_user(next(iter(target.allowed_users)), status='suspended')
        elif change == 'feature':
            context.private_context_enabled = False
        else:
            actor = next(iter(target.allowed_users))
            credential = context.auth_service.org_store._db.execute("SELECT id FROM api_credential WHERE user_id=?", (actor,)).fetchone()[0]
            assert context.auth_service.revoke_credential(credential)
        return value

    monkeypatch.setattr(context_private, '_body', altered)
    result = create(client, headers)
    assert result.status_code in (401,403,503)
    assert store.db.execute('SELECT count(*) FROM private_context_v1').fetchone()[0] == 0


def test_quota_rollback_and_withdrawal_available_when_full(private):
    client, store, headers, _, _ = private
    own = create(client, headers).json()
    store.db.execute('UPDATE private_context_budget_v1 SET bytes=268435456')
    store.db.commit()
    assert create(client, headers,eventId='new').status_code == 413
    assert client.put('/v1/context-private/'+own['id'],headers=headers,
                      json={'expectedRevision':1,'text':'new'}).status_code == 413
    assert client.get('/v1/context-private/'+own['id'],headers=headers).json()['revision'] == 1
    assert client.post('/v1/context-private/'+own['id']+'/withdraw',headers=headers,
                       json={'expectedRevision':1}).json()['withdrawn'] is True


def test_migration_failure_rolls_back_all_ddl(setup, monkeypatch):
    import sqlite3
    from engine import context_private
    from fastapi import HTTPException

    _, store, _, _, _ = setup
    original = sqlite3.connect

    class Fault(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if 'CREATE TABLE IF NOT EXISTS private_context_revisions' in sql:
                raise sqlite3.OperationalError('synthetic DDL failure')
            return super().execute(sql, parameters)

    monkeypatch.setattr(context_private.sqlite3, 'connect', lambda *args, **kwargs: original(*args, **kwargs, factory=Fault))
    with pytest.raises(HTTPException) as failure:
        PrivateContextJournal(store.path)
    assert failure.value.status_code == 503
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'private_context_%'").fetchone()
    assert store.count() == 0


def test_writer_lock_timeout_leaves_backup_and_private_journal_unchanged(private):
    client, store, headers, _, _ = private
    store.db.execute('BEGIN IMMEDIATE')
    try:
        assert create(client, headers).status_code == 503
    finally:
        store.db.rollback()
    assert create(client, headers).status_code == 200
    assert store.count() == 0


def test_revision_limit_preserves_withdrawal_and_cannot_overflow_protocol(private):
    client, store, headers, _, _ = private
    own = create(client, headers).json()
    identifier = own['id']
    store.db.execute('INSERT INTO private_context_revisions_v1 SELECT id,100,text,tags,repository,changed FROM private_context_revisions_v1 WHERE id=? AND revision=1', (identifier,))
    store.db.execute('UPDATE private_context_v1 SET revision=100 WHERE id=?',(identifier,))
    store.db.execute('UPDATE private_context_budget_v1 SET bytes=268435456,records=10000')
    store.db.commit()
    route = '/v1/context-private/'+identifier
    assert client.put(route,headers=headers,json={'expectedRevision':100,'text':'new'}).status_code == 409
    terminal = client.post(route+'/withdraw',headers=headers,json={'expectedRevision':100}).json()
    assert terminal['withdrawn'] and terminal['revision'] == 101 and 'text' not in terminal
    assert check(client,headers,identifier,100).json()['valid'] is False


def test_concurrent_edit_compare_and_swap_one_revision(private):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import HTTPException
    from engine.auth import Principal
    from engine.context_private import PrivateEdit

    client, store, headers, target, context = private
    own = create(client,headers).json()
    principal = Principal(next(iter(target.allowed_users)), target.org_id, 'member', 'bearer')

    def edit(text):
        try:
            return context.private_context_store.change(context,principal,own['id'],PrivateEdit(expectedRevision=1,text=text))['revision']
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit,['first','second']))
    assert sorted(results) == [2,409]
    assert store.db.execute('SELECT count(*) FROM private_context_revisions_v1 WHERE id=?',(own['id'],)).fetchone()[0] == 2


def test_private_golden_is_actual_producer_shape(private, monkeypatch):
    from pathlib import Path

    client, store, headers, _, _ = private
    fixture = json.loads((Path(__file__).parent/'fixtures/context-private-v1.json').read_text())
    from engine import context_private
    monkeypatch.setattr(context_private.time, 'time', lambda: datetime.fromisoformat(fixture['source']['createdAt']).timestamp())
    source = create(client,headers,tags=['question']).json()
    identifier = source['id']
    actual = client.get('/v1/context-private/'+identifier, headers=headers).json()
    actual['id'] = fixture['source']['id']  # Random owner-scoped identifier; every other wire field unchanged.
    assert actual == fixture['source']
    terminal = client.post('/v1/context-private/'+identifier+'/withdraw',headers=headers,
                           json={'expectedRevision':1}).json()
    terminal['id'] = fixture['withdrawn']['id']
    assert terminal == fixture['withdrawn']
    assert check(client,headers,identifier,1).json() == fixture['references']


def test_unicode_scalar_limit_and_invalid_surrogate_no_loggable_encode_failure(private):
    client, _, headers, _, _ = private
    assert create(client,headers,text='😀'*8192).status_code == 200
    assert create(client,headers,eventId='large',text='😀'*8193).status_code == 422
    result = client.post('/v1/context-private',headers=headers,
                         content='{"eventId":"invalid","text":"\\ud800"}')
    assert result.status_code == 422 and 'd800' not in result.text


def test_per_actor_budget_preserves_other_owner_and_terminal_withdrawal(private):
    client, store, headers, target, context = private
    own = create(client,headers).json()
    store.db.execute('UPDATE private_context_actor_budget_v1 SET bytes=33554432')
    store.db.commit()
    assert create(client,headers,eventId='new').status_code == 413
    assert client.put('/v1/context-private/'+own['id'],headers=headers,json={'expectedRevision':1,'text':'new'}).status_code == 413
    assert create(client,other_headers(context)).status_code == 200
    assert client.post('/v1/context-private/'+own['id']+'/withdraw',headers=headers,json={'expectedRevision':1}).json()['withdrawn']
    assert store.db.execute('SELECT bytes FROM private_context_actor_budget_v1 WHERE actor=?',(next(iter(target.allowed_users)),)).fetchone()[0] == 33554432


def test_revision_rows_immutable_inside_application_database(private):
    import sqlite3

    client, store, headers, _, _ = private
    own = create(client,headers).json()
    for sql in ['UPDATE private_context_revisions_v1 SET text=\'overwrite\' WHERE id=?',
                'DELETE FROM private_context_revisions_v1 WHERE id=?']:
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(sql,(own['id'],))
        store.db.rollback()
    assert client.get('/v1/context-private/'+own['id'],headers=headers).json()['text'] == own['text']


def test_nul_rejected_search_body_and_corrupt_source_reference_gate(private):
    client, store, headers, _, _ = private
    assert create(client,headers,text='a\x00b').status_code == 422
    own = create(client,headers).json()
    assert client.post('/v1/context-private-search',headers=headers,json=window(q='a\x00b')).status_code == 422
    store.db.execute('DROP TRIGGER private_revision_no_update_v1')  # Isolated corruption fixture, not production.
    store.db.execute('UPDATE private_context_revisions_v1 SET text=? WHERE id=?',('a\x00b',own['id']))
    store.db.commit()
    assert check(client,headers,own['id'],1).json()['valid'] is False
    assert client.get('/v1/context-private/'+own['id'],headers=headers).status_code == 503


def test_late_worker_identity_does_not_share_org_writer_connection(private, monkeypatch):
    from engine.auth import Principal
    from engine.context_private import PrivateEdit

    client, _, headers, target, context = private
    own = create(client,headers).json()
    principal = Principal(next(iter(target.allowed_users)),target.org_id,'member','bearer')

    def forbidden_shared_read(user):
        raise AssertionError('private worker must use isolated identity read, not OrgStore writer')

    monkeypatch.setattr(context.auth_service.org_store,'get_user',forbidden_shared_read)
    changed = context.private_context_store.change(context,principal,own['id'],PrivateEdit(expectedRevision=1,text='isolated'))
    assert changed['revision'] == 2
    context.auth_service.org_store._db.execute("UPDATE user SET status='suspended' WHERE id=?", (principal.user_id,))
    context.auth_service.org_store._db.commit()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as denied:
        context.private_context_store.change(context,principal,own['id'],PrivateEdit(expectedRevision=2,text='forbidden'))
    assert denied.value.status_code == 403


def test_twenty_controlled_concurrent_revisions_no_false_actor_denial(private):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from engine.auth import Principal
    from engine.context_private import PrivateEdit
    from fastapi import HTTPException

    client, _, headers, target, context = private
    principal = Principal(next(iter(target.allowed_users)),target.org_id,'member','bearer')
    for number in range(20):
        own = create(client,headers,eventId=f'concurrent-{number}').json()
        barrier = Barrier(2)

        def edit(text):
            barrier.wait(timeout=5)
            try:
                return context.private_context_store.change(context,principal,own['id'],PrivateEdit(expectedRevision=1,text=text))['revision']
            except HTTPException as error:
                return error.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit,['first','second']))
        assert sorted(results) == [2,409], results


@pytest.mark.parametrize('operation', ['edit','withdraw','references','search'])
def test_each_body_route_rechecks_feature_after_await(private, monkeypatch, operation):
    from engine import context_private
    client, store, headers, _, context = private
    own = create(client,headers).json()
    original = context_private._body

    async def disable(request, model):
        value = await original(request,model)
        context.private_context_enabled = False
        return value

    monkeypatch.setattr(context_private,'_body',disable)
    if operation == 'edit':
        result = client.put('/v1/context-private/'+own['id'],headers=headers,json={'expectedRevision':1,'text':'new'})
    elif operation == 'withdraw':
        result = client.post('/v1/context-private/'+own['id']+'/withdraw',headers=headers,json={'expectedRevision':1})
    elif operation == 'references':
        result = check(client,headers,own['id'],1)
    else:
        result = client.post('/v1/context-private-search',headers=headers,json=window(q='private'))
    assert result.status_code == 503
    row = store.db.execute('SELECT revision,withdrawn FROM private_context_v1 WHERE id=?',(own['id'],)).fetchone()
    assert tuple(row) == (1,0)


def test_edit_tag_validation_cannot_upgrade_evidence(private):
    client, _, headers, _, _ = private
    own = create(client,headers).json()
    for tags in [['verification','verification'],['verified-complete']]:
        result = client.put('/v1/context-private/'+own['id'],headers=headers,json={'expectedRevision':1,'text':'update','tags':tags})
        assert result.status_code == 422 and 'update' not in result.text
    revised = client.put('/v1/context-private/'+own['id'],headers=headers,
                         json={'expectedRevision':1,'text':'verification intent','tags':['verification']}).json()
    assert revised['evidenceKind'] == 'user-statement' and 'verified' not in revised


def test_private_http_scope_checks_never_use_org_shared_user_reader(private, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, _, headers, _, context = private
    own = create(client,headers).json()

    def forbidden(user):
        raise AssertionError('private request pre/post/GET must not use shared user reader')

    monkeypatch.setattr(context.auth_service.org_store,'get_user',forbidden)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for number in range(10):
            event = create(client,headers,eventId=f'http-concurrent-{number}').json()
            barrier = Barrier(2)

            def edit(text):
                barrier.wait(timeout=5)
                return client.put('/v1/context-private/'+event['id'],headers=headers,
                                  json={'expectedRevision':1,'text':text}).status_code

            assert sorted(pool.map(edit,['first','second'])) == [200,409]
            reads = list(pool.map(lambda _: client.get('/v1/context-private/'+own['id'],headers=headers).status_code,[0,1]))
            assert reads == [200,200]
    assert client.post('/v1/context-private-search',headers=headers,json=window(q='parser')).status_code == 200
    assert check(client,headers,own['id'],1).json()['valid'] is True


def test_project_identifier_matches_shared_eighty_character_boundary(private):
    client, _, headers, _, _ = private
    assert create(client, headers, projectId='x' * 81).status_code == 422
    assert client.post('/v1/context-private-search', headers=headers,
                       json=window(projectId='x' * 81)).status_code == 422
    # Syntactically valid but unconfigured catalog ID fails authorization.
    assert create(client, headers, projectId='x' * 80).status_code == 403


@pytest.mark.parametrize('origin', ['https://untrusted.example', 'null', ''])
def test_private_browser_origin_rejected_even_with_valid_credential(private, origin):
    client, _, headers, _, _ = private
    own = create(client, headers).json()
    browser = {**headers, 'Origin': origin}
    requests = [
        lambda: create(client, browser),
        lambda: listing(client, browser),
        lambda: client.get('/v1/context-private/'+own['id'], headers=browser),
        lambda: client.put('/v1/context-private/'+own['id'], headers=browser,
                           json={'expectedRevision': 1, 'text': 'changed'}),
        lambda: client.post('/v1/context-private/'+own['id']+'/withdraw', headers=browser,
                            json={'expectedRevision': 1}),
        lambda: client.post('/v1/context-private-search', headers=browser, json=window()),
        lambda: check(client, browser, own['id'], 1),
    ]
    for request in requests:
        response = request()
        assert response.status_code == 403
        assert response.json()['detail'] == 'NATIVE_CLIENT_REQUIRED'
    assert check(client, headers, own['id'], 1).json()['valid'] is True


def test_event_lookup_owner_scoped_and_not_absence_proof(private):
    client, store, headers, _, context = private
    unknown = client.get('/v1/context-private-events/personal-1', headers=headers)
    assert unknown.status_code == 200
    assert unknown.json() == {'version': 1, 'meaning': 'point-in-time-only-not-absence-proof',
                              'status': 'not-observed', 'source': None, 'mutationId': None}
    created = create(client, headers).json()
    observed = client.get('/v1/context-private-events/personal-1', headers=headers)
    assert observed.status_code == 200 and observed.json()['source'] == created
    for role in ['member', 'admin']:
        other = client.get('/v1/context-private-events/personal-1', headers=other_headers(context, role))
        assert other.status_code == 200 and other.json()['source'] is None
    changed = client.put('/v1/context-private/' + created['id'], headers=headers,
                         json={'expectedRevision': 1, 'text': 'new revision', 'tags': []}).json()
    assert changed['revision'] == 2
    assert client.get('/v1/context-private-events/personal-1', headers=headers).json()['source'] == created
    for origin in ['', 'null', 'https://example.test']:
        assert client.get('/v1/context-private-events/personal-1', headers={**headers, 'Origin': origin}).status_code == 403
    assert client.get('/v1/context-private-events/personal-1?q=x', headers=headers).status_code == 422
    assert client.get('/v1/context-private-events/personal%0A', headers=headers).status_code == 422
    assert store.db.execute('SELECT count(*) FROM backup_records').fetchone()[0] == 0


def test_event_lookup_project_revoke_and_other_org(private):
    client, _, headers, target, context = private
    actor = next(iter(target.allowed_users))
    context.projects = (ContextProject(id='vox', org_id=target.org_id, name='Vox', repository_id='team/repo', allowed_users=(actor,)),)
    assert create(client, headers, projectId='vox').status_code == 200
    assert client.get('/v1/context-private-events/personal-1', headers=other_headers(context, org='separate-org')).json()['source'] is None
    context.projects = ()
    result = client.get('/v1/context-private-events/personal-1', headers=headers)
    assert result.status_code == 404 and 'source' not in result.json()


@pytest.mark.parametrize('change', ['feature', 'credential', 'suspend'])
def test_event_lookup_late_identity_narrowing(private, monkeypatch, change):
    client, _, headers, target, context = private
    assert create(client, headers).status_code == 200
    original = context.private_context_store.own
    changed_once = False

    def altered(*args):
        nonlocal changed_once
        row = original(*args)
        if changed_once:
            return row
        changed_once = True
        actor = next(iter(target.allowed_users))
        if change == 'feature':
            context.private_context_enabled = False
        elif change == 'suspend':
            context.auth_service.org_store.update_user(actor, status='suspended')
        else:
            credential = context.auth_service.org_store._db.execute('SELECT id FROM api_credential WHERE user_id=?', (actor,)).fetchone()[0]
            assert context.auth_service.revoke_credential(credential)
        return row

    monkeypatch.setattr(context.private_context_store, 'own', altered)
    result = client.get('/v1/context-private-events/personal-1', headers=headers)
    assert result.status_code in (401, 403, 503) and 'source' not in result.json()


def test_mutation_proof_survives_new_revision_without_replay(private):
    client, store, headers, _, context = private
    own = create(client, headers).json()
    path = '/v1/context-private/' + own['id']
    body = {'requestId': 'mutation-1', 'expectedRevision': 1, 'text': 'edited', 'tags': []}
    first = client.put(path, headers=headers, json=body)
    assert first.status_code == 200 and first.json()['revision'] == 2
    later = client.put(path, headers=headers, json={'requestId': 'mutation-2', 'expectedRevision': 2, 'text': 'later', 'tags': []})
    assert later.status_code == 200 and later.json()['revision'] == 3
    replay = client.put(path, headers=headers, json=body)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert client.get(path, headers=headers).json() == later.json()
    proof = client.get('/v1/context-private-events/personal-1/requests/mutation-1', headers=headers)
    assert proof.status_code == 200 and proof.json()['source'] == first.json() and proof.json()['mutationId'] == 'mutation-1'
    assert proof.headers['cache-control'] == 'no-store'
    assert client.put(path, headers=headers, json={**body, 'text': 'different'}).status_code == 409
    missing = client.get('/v1/context-private-events/personal-1/requests/missing', headers=headers)
    assert missing.json()['status'] == 'not-observed' and missing.json()['source'] is None
    assert client.get('/v1/context-private-events/personal-1/requests/mutation-1', headers=other_headers(context, 'admin')).json()['source'] is None
    withdrawal = {'requestId': 'withdrawal-1', 'expectedRevision': 3}
    result = client.post(path + '/withdraw', headers=headers, json=withdrawal)
    assert result.status_code == 200 and result.json()['withdrawn']
    assert client.post(path + '/withdraw', headers=headers, json=withdrawal).json() == result.json()
    assert client.post(path + '/withdraw', headers=headers, json={**withdrawal, 'requestId': 'other-withdrawal'}).status_code == 409
    assert store.db.execute('SELECT count(*) FROM private_context_mutations_v1').fetchone()[0] == 3


def test_same_mutation_concurrent_retry_is_one_revision(private):
    from concurrent.futures import ThreadPoolExecutor
    client, store, headers, _, _ = private
    own = create(client, headers).json()
    body = {'requestId': 'concurrent-original', 'expectedRevision': 1, 'text': 'one mutation', 'tags': []}
    path = '/v1/context-private/' + own['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.put(path, headers=headers, json=body), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert store.db.execute('SELECT count(*) FROM private_context_mutations_v1').fetchone()[0] == 1
    assert client.get(path, headers=headers).json()['revision'] == 2
    different = create(client, headers, eventId='other-event').json()
    assert client.put('/v1/context-private/' + different['id'], headers=headers, json=body).status_code == 409
    assert client.put(path, headers=headers, json={**body, 'requestId': 'bad\n'}).status_code == 422


def test_mutation_proofs_are_immutable(private):
    import sqlite3
    client, store, headers, _, _ = private
    own = create(client, headers).json()
    assert client.put('/v1/context-private/' + own['id'], headers=headers,
                      json={'requestId': 'immutable-proof', 'expectedRevision': 1, 'text': 'changed'}).status_code == 200
    for statement in ('UPDATE private_context_mutations_v1 SET revision=1', 'DELETE FROM private_context_mutations_v1'):
        with pytest.raises(sqlite3.IntegrityError, match='PRIVATE_MUTATION_IMMUTABLE'):
            store.db.execute(statement)
        store.db.rollback()
    result = client.get('/v1/context-private-events/personal-1/requests/immutable-proof', headers=headers)
    assert result.status_code == 200 and result.json()['source']['revision'] == 2


def test_event_creation_history_and_terminal_body_denial(private):
    client, _, headers, _, _ = private
    original = create(client, headers).json()
    path = '/v1/context-private/' + original['id']
    edit = {'requestId': 'edit-history', 'expectedRevision': 1, 'text': 'newer', 'tags': []}
    assert client.put(path, headers=headers, json=edit).status_code == 200
    proof = client.get('/v1/context-private-events/personal-1', headers=headers)
    assert proof.json()['source'] == original
    assert client.get(path, headers=headers).json()['revision'] == 2
    assert client.post(path + '/withdraw', headers=headers, json={'requestId': 'terminal-proof', 'expectedRevision': 2}).status_code == 200
    for result in (client.get('/v1/context-private-events/personal-1', headers=headers),
                   client.get('/v1/context-private-events/personal-1/requests/edit-history', headers=headers),
                   client.put(path, headers=headers, json=edit)):
        assert result.status_code == 200 and result.json().get('source', result.json())['withdrawn']
        assert 'text' not in result.json().get('source', result.json())


def test_private_input_readonly_authorization(private, setup):
    client, store, headers, _, context = private
    response = client.get('/v1/context-private-authorization', headers=headers)
    assert response.status_code == 200
    assert response.json() == {'version': 1, 'allowed': True, 'meaning': 'current-private-intake-authorization'}
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/v1/context-private-authorization', headers={}).status_code == 401
    assert client.get('/v1/context-private-authorization?text=never', headers=headers).status_code == 422
    assert client.get('/v1/context-private-authorization', headers={**headers, 'Origin': 'https://example.test'}).status_code == 403
    assert store.count() == 0
    assert context.private_context_store.connection is not None
    assert setup[0].get('/v1/context-private-authorization', headers=headers).status_code == 404


@pytest.mark.parametrize('change', ['feature', 'suspend', 'credential'])
def test_input_authorization_rechecks_current_identity(private, monkeypatch, change):
    client, store, headers, target, context = private
    original = context.private_context_store.authorize
    changed = False

    def narrow(*args):
        nonlocal changed
        original(*args)
        if changed:
            return
        changed = True
        actor = next(iter(target.allowed_users))
        if change == 'feature':
            context.private_context_enabled = False
        elif change == 'suspend':
            context.auth_service.org_store.update_user(actor, status='suspended')
        else:
            credential = context.auth_service.org_store._db.execute(
                'SELECT id FROM api_credential WHERE user_id=?', (actor,)).fetchone()[0]
            assert context.auth_service.revoke_credential(credential)

    monkeypatch.setattr(context.private_context_store, 'authorize', narrow)
    response = client.get('/v1/context-private-authorization', headers=headers)
    assert response.status_code in (401, 403, 503)
    assert 'allowed' not in response.json()
    assert store.count() == 0


def test_expected_repository_rejects_remap_before_write_and_replays_snapshot(private):
    client, store, headers, target, context = private
    actor = next(iter(target.allowed_users))
    context.projects = (ContextProject(id='vox', org_id=target.org_id, name='Vox', repository_id='team/repo', allowed_users=(actor,)),)
    assert create(client, headers, projectId='vox', expectedRepositoryId='team/other').status_code == 409
    assert listing(client, headers).json()['items'] == []
    own = create(client, headers, projectId='vox', expectedRepositoryId='team/repo')
    assert own.status_code == 200 and own.json()['repositoryId'] == 'team/repo'
    context.projects = (context.projects[0].model_copy(update={'repository_id':'team/renamed'}),)
    assert create(client, headers, projectId='vox', expectedRepositoryId='team/repo').json() == own.json()
    assert create(client, headers, eventId='new', projectId='vox', expectedRepositoryId='team/repo').status_code == 409
    assert create(client, headers, projectId='vox', expectedRepositoryId='team/renamed').status_code == 409
    assert len(listing(client, headers).json()['items']) == 1
    assert store.count() == 0


@pytest.mark.parametrize('repository', ['team/repo\n', 'file:///repo', '../repo', 'team/repo/path', True, '', 'x' * 201])
def test_expected_repository_snapshot_validation(private, repository):
    client, _, headers, _, _ = private
    assert create(client, headers, projectId='vox', expectedRepositoryId=repository).status_code == 422


def test_repository_snapshot_requires_project_and_preserves_legacy_fingerprint(private):
    import hashlib
    client, _, headers, _, context = private
    assert create(client, headers, expectedRepositoryId='team/repo').status_code == 422
    own = create(client, headers)
    assert own.status_code == 200
    legacy = {'eventId':'personal-1','text':'需要修复 parser','tags':[], 'projectId':None,'visibility':'private'}
    fingerprint = hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    assert fingerprint == 'd9238de98473f6f0c0abfcb39972e308c9940f960a1c4a2b18d807b365cee1f3'
    with context.private_context_store.connection() as db:
        assert db.execute('SELECT fingerprint FROM private_context_v1').fetchone()[0] == fingerprint
    assert create(client, headers, expectedRepositoryId=None).json() == own.json()


def test_snapshot_denial_precedes_repository_details_and_replay_revocation(private, monkeypatch):
    client, _, headers, target, context = private
    actor = next(iter(target.allowed_users))
    context.projects = (ContextProject(id='vox', org_id=target.org_id, name='Vox', repository_id='team/repo', allowed_users=(actor,)),)
    journal = context.private_context_store
    reservations = []
    original = journal.reserve
    monkeypatch.setattr(journal, 'reserve', lambda *args, **kwargs: (reservations.append(True), original(*args, **kwargs))[1])
    mismatch = create(client, headers, projectId='vox', expectedRepositoryId='team/other')
    assert mismatch.status_code == 409 and mismatch.json()['detail'] == 'PRIVATE_CONTEXT_PROJECT_CHANGED'
    assert reservations == []
    with journal.connection() as db:
        assert db.execute('SELECT count(*) FROM private_context_v1').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM private_context_revisions_v1').fetchone()[0] == 0
    own = create(client, headers, projectId='vox', expectedRepositoryId='team/repo')
    assert own.status_code == 200 and len(reservations) == 1
    context.projects = ()
    replay = create(client, headers, projectId='vox', expectedRepositoryId='team/repo')
    assert replay.status_code == 404
    denied = create(client, headers, eventId='new', projectId='vox', expectedRepositoryId='team/other')
    assert denied.status_code == 403 and denied.json()['detail'] == 'PRIVATE_CONTEXT_PROJECT_DENIED'
    assert len(reservations) == 1
