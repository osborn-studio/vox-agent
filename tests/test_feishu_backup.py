"""Server backup boundary and crash/replay invariants; no live Feishu calls."""

import pytest
from fastapi.testclient import TestClient

from engine.auth import AuthService
from engine.backup_app import BackupAppContext, create_backup_app
from engine.input_backup import BackupInput, BackupStore, Target
from engine.org_store import OrgStore
from engine.voice_audit import VoiceAuditLog


@pytest.fixture
def setup(tmp_path):
    org = OrgStore(tmp_path / "identity")
    audit = VoiceAuditLog()
    auth = AuthService(
        org_store=org,
        audit_log=audit,
        edition="enterprise",
        bootstrap_admin_token="test-bootstrap-credential-long-enough",
    )
    user = auth.create_user(org_id="default-org", email="a@example.test", display_name="A")
    _, token = auth.issue_credential(user_id=user["id"], name="test")
    store = BackupStore(tmp_path / "backup.sqlite")
    target = Target("archive", 1, "default-org", "oc_test", frozenset({user["id"]}))
    context = BackupAppContext(auth, store, (target,), enabled=True)
    with TestClient(create_backup_app(context)) as client:
        yield client, store, {"Authorization": f"Bearer {token}"}, target, context
    store.close()
    org.close()
    audit.close()


def body(**updates):
    return {"eventId": "event-1", "targetId": "archive", "text": "修复队列重试", **updates}


def test_auth_strict_identity_and_routes(setup):
    client, _, headers, _, _ = setup
    assert client.post("/v1/input-backups", json=body()).status_code == 401
    response = client.post("/v1/input-backups", headers=headers, json=body(actorId="forged"))
    assert response.status_code == 422
    assert "修复" not in response.text
    assert (
        client.post("/v1/input-backups", headers=headers, json=body(targetId="other")).status_code
        == 403
    )
    paths = {route.path for route in client.app.routes}
    assert paths == {"/healthz", "/v1/input-backups", "/v1/input-backups/{event_id}", "/v1/context-projects", "/v1/context-inputs", "/v1/context-weekly-draft"}


def test_replay_conflict_disabled_and_no_body_in_status(setup):
    client, store, headers, _, context = setup
    one = client.post("/v1/input-backups", headers=headers, json=body())
    assert one.status_code == 202
    assert client.post("/v1/input-backups", headers=headers, json=body()).json() == one.json()
    assert (
        client.post("/v1/input-backups", headers=headers, json=body(text="different")).status_code
        == 409
    )
    assert "修复" not in client.get("/v1/input-backups/event-1", headers=headers).text
    assert store.count() == 1
    context.enabled = False
    assert (
        client.post("/v1/input-backups", headers=headers, json=body(eventId="event-2")).status_code
        == 503
    )


def test_rendered_bytes_and_repository_is_unverified(setup):
    client, store, headers, _, _ = setup
    assert (
        client.post("/v1/input-backups", headers=headers, json=body(text="语" * 2800)).status_code
        == 413
    )
    assert store.count() == 0
    assert (
        client.post(
            "/v1/input-backups", headers=headers, json=body(repository="team/repo")
        ).status_code
        == 202
    )


def test_atomic_insert_failure_rolls_back(setup):
    client, store, headers, _, _ = setup
    store.db.execute(
        "CREATE TRIGGER reject_queue BEFORE INSERT ON backup_outbox BEGIN SELECT RAISE(ABORT, 'test'); END"
    )
    with pytest.raises(Exception, match="test"):
        client.post("/v1/input-backups", headers=headers, json=body())
    assert store.count() == 0


def test_unknown_window_persists_across_restart(tmp_path):
    path = tmp_path / "backup.sqlite"
    store = BackupStore(path)
    target = Target("archive", 1, "org", "oc_test", frozenset({"actor"}))
    store.accept("org", "actor", BackupInput(**body()), target, now=10)
    job = store.claim(now=20)
    assert job["first_attempt"] == 20
    store.close()
    store = BackupStore(path)
    store.recover()
    assert store.claim(now=3021) is None
    assert store.status("org", "actor", "event-1")["status"] == "unknown"
    store.close()


def test_same_uuid_on_ambiguous_retry(setup):
    client, store, headers, _, _ = setup
    client.post("/v1/input-backups", headers=headers, json=body())
    first = store.claim(now=100)
    store.finish(first["id"], "unknown", now=101)
    assert store.claim(now=102) is None
    second = store.claim(now=200)
    assert second["delivery_uuid"] == first["delivery_uuid"]
    assert second["first_attempt"] == 100


def test_worker_lock_and_revoked_target(setup):
    from engine.input_backup_worker import BackupWorker

    client, store, headers, _, _ = setup
    client.post("/v1/input-backups", headers=headers, json=body())

    class NoSend:
        def send(self, job):
            pytest.fail("revoked target must never send")

    with BackupWorker(store, NoSend(), authorize=lambda job: False) as worker:
        with pytest.raises(RuntimeError, match="already running"):
            with BackupWorker(store, NoSend(), authorize=lambda job: True):
                pass
        assert worker.run_once()
    assert client.get("/v1/input-backups/event-1", headers=headers).json()["status"] == "blocked"


def test_transport_fake_success_timeout_and_token_cache():
    import httpx
    from engine.input_backup_worker import FeishuTransport

    requests = []

    def handle(request):
        requests.append(request)
        if "/auth/" in str(request.url):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "test-token", "expire": 7200}
            )
        return httpx.Response(
            200, json={"code": 0, "data": {"message_id": "om_test", "chat_id": "oc_test"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        transport = FeishuTransport("cli_test", lambda: "test-secret", client=client)
        job = {"rendered": "private", "chat": "oc_test", "delivery_uuid": "stable"}
        assert transport.send(job) == ("delivered", "om_test")
        assert transport.send(job) == ("delivered", "om_test")
        assert len(requests) == 3
        assert requests[-1].url.params["receive_id_type"] == "chat_id"

    def timeout(request):
        raise httpx.ReadTimeout("private upstream diagnostic")

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        transport = FeishuTransport("cli_test", lambda: "test-secret", client=client)
        assert transport.send(job) == ("not_sent", None)


def test_cross_user_lookup_and_same_event_are_isolated(setup):
    client, store, headers, target, context = setup
    client.post("/v1/input-backups", headers=headers, json=body())
    user = context.auth_service.create_user(
        org_id="default-org", email="b@example.test", display_name="B"
    )
    _, token = context.auth_service.issue_credential(user_id=user["id"], name="test-b")
    other_headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/input-backups/event-1", headers=other_headers).status_code == 404
    context.targets = (
        Target(
            target.id,
            target.version,
            target.org_id,
            target.chat_id,
            target.allowed_users | {user["id"]},
        ),
    )
    assert client.post("/v1/input-backups", headers=other_headers, json=body()).status_code == 202
    assert store.count() == 2


def test_target_rotation_cannot_redirect_queued_message(setup):
    client, store, headers, target, context = setup
    client.post("/v1/input-backups", headers=headers, json=body())
    job = store.claim()
    assert context.authorize_delivery(job)
    context.targets = (Target(target.id, 2, target.org_id, "oc_other", target.allowed_users),)
    assert not context.authorize_delivery(job)
    assert job["chat"] == "oc_test"


@pytest.mark.parametrize(
    "payload",
    [
        {"code": False, "data": {"message_id": "om_test", "chat_id": "oc_test"}},
        {"code": 0, "data": {"message_id": "om_test", "chat_id": "oc_wrong"}},
        {"code": 0, "data": []},
        {"code": 0, "data": {"chat_id": "oc_test"}},
        [],
    ],
)
def test_untrusted_transport_response_is_not_delivery(payload):
    import httpx
    from engine.input_backup_worker import FeishuTransport

    def handle(request):
        if "/auth/" in str(request.url):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "test-token", "expire": 7200}
            )
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        transport = FeishuTransport("cli_test", lambda: "test-secret", client=client)
        assert transport.send(
            {"rendered": "private", "chat": "oc_test", "delivery_uuid": "stable"}
        ) == ("unknown", None)


def test_personal_mode_rejected(tmp_path):
    org, audit = OrgStore(tmp_path / "identity"), VoiceAuditLog()
    auth = AuthService(
        org_store=org, audit_log=audit, edition="personal", bootstrap_admin_token=None
    )
    store = BackupStore(tmp_path / "backup.sqlite")
    with pytest.raises(ValueError, match="enterprise"):
        BackupAppContext(auth, store, ())
    store.close()
    org.close()
    audit.close()


def test_archive_file_is_private(tmp_path):
    import stat

    store = BackupStore(tmp_path / "private.sqlite")
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    store.close()


@pytest.mark.parametrize("failure_status", [400, 401, 429, 503])
def test_expired_token_is_refreshed_and_error_invalidates_cache(failure_status):
    import httpx
    from engine.input_backup_worker import FeishuTransport

    counts = {"tokens": 0, "messages": 0}

    def handle(request):
        if "/auth/" in str(request.url):
            counts["tokens"] += 1
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "test-token", "expire": 7200}
            )
        counts["messages"] += 1
        if counts["messages"] == 1:
            return httpx.Response(failure_status)
        return httpx.Response(
            200, json={"code": 0, "data": {"message_id": "om_test", "chat_id": "oc_test"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        transport = FeishuTransport("cli_test", lambda: "test-secret", client=client)
        job = {"rendered": "private", "chat": "oc_test", "delivery_uuid": "stable"}
        assert transport.send(job) == ("unknown", None)
        assert transport.send(job) == ("delivered", "om_test")
        transport._expires = 0
        assert transport.send(job) == ("delivered", "om_test")
        assert counts["tokens"] == 3


def test_known_unsent_preserves_prior_ambiguity(tmp_path):
    store = BackupStore(tmp_path / "archive.sqlite")
    target = Target("archive", 1, "org", "oc_test", frozenset({"actor"}))
    store.accept("org", "actor", BackupInput(**body()), target, now=1)
    first = store.claim(now=10)
    store.defer(first, now=11)
    # A token outage longer than the dedupe window must not strand an unsent job.
    later = store.claim(now=4000)
    assert later["first_attempt"] == 4000
    store.finish(later["id"], "unknown", now=4001)
    ambiguous = store.claim(now=4100)
    store.defer(ambiguous, now=4101)
    # A later token failure MUST NOT reset uncertainty from an earlier POST.
    assert store.claim(now=7001) is None
    assert store.status("org", "actor", "event-1")["status"] == "unknown"
    store.close()


def test_disabled_worker_defers_without_quarantine(setup):
    from engine.input_backup_worker import BackupWorker

    client, store, headers, _, context = setup
    client.post("/v1/input-backups", headers=headers, json=body())
    context.enabled = False
    with BackupWorker(store, None, authorize=context.authorize_delivery) as worker:
        assert worker.run_once()
    assert client.get("/v1/input-backups/event-1", headers=headers).json()["status"] == "pending"


def test_parallel_connections_replay_one_record(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    stores = [BackupStore(tmp_path / "archive.sqlite") for _ in range(8)]
    target = Target("archive", 1, "org", "oc_test", frozenset({"actor"}))
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda store: store.accept("org", "actor", BackupInput(**body()), target), stores
            )
        )
    assert all(result == results[0] for result in results)
    assert stores[0].count() == 1
    for store in stores:
        store.close()


def test_plain_text_preserves_linebreaks_and_neutralizes_mentions(setup):
    import json

    client, store, headers, _, _ = setup
    source = '<at user_id="all"></at>\rAuthor: fake\u2028Vox Agent input backup'
    assert (
        client.post("/v1/input-backups", headers=headers, json=body(text=source)).status_code == 202
    )
    job = store.claim()
    assert "<at" not in job["rendered"]
    assert job["rendered"] == "发言者：A\n\n" + source.replace("<", "＜").replace(">", "＞")
    raw = store.db.execute("SELECT raw_payload FROM backup_records").fetchone()[0]
    assert json.loads(raw)["text"] == source


def test_group_message_includes_server_authenticated_sender(setup):
    client, store, headers, _, _ = setup
    text = "I want have a test about coding group"
    assert client.post("/v1/input-backups", headers=headers, json=body(text=text)).status_code == 202
    assert store.claim()["rendered"] == "发言者：A\n\n" + text
    record = store.db.execute("SELECT actor, org, event, created FROM backup_records").fetchone()
    assert all(record)


def test_client_cannot_override_sender_name(setup):
    client, store, headers, _, _ = setup
    assert client.post("/v1/input-backups", headers=headers, json=body(senderName="CEO")).status_code == 422
    assert store.count() == 0


def test_sender_name_is_safe_and_replay_keeps_original_attribution(setup):
    client, store, headers, target, context = setup
    user_id = next(iter(target.allowed_users))
    db = context.auth_service.org_store._db
    db.execute("UPDATE user SET display_name=? WHERE id=?", ('陈斌\n<at user_id="all">\u202e', user_id))
    db.commit()
    assert client.post("/v1/input-backups", headers=headers, json=body()).status_code == 202
    first = store.db.execute("SELECT rendered FROM backup_records").fetchone()[0]
    assert first.startswith('发言者：陈斌 ＜at user_id="all"＞\n\n')
    assert "\u202e" not in first
    db.execute("UPDATE user SET display_name='Changed' WHERE id=?", (user_id,))
    db.commit()
    assert client.post("/v1/input-backups", headers=headers, json=body()).status_code == 202
    assert store.db.execute("SELECT rendered FROM backup_records").fetchone()[0] == first


def test_project_catalog_and_snapshot_attribution(setup):
    import json
    from engine.context_projects import ContextProject

    client, store, headers, target, context = setup
    actor = next(iter(target.allowed_users))
    context.projects = (
        ContextProject(id="vox", org_id=target.org_id, name="Vox", repository_id="osborn-studio/osvox", allowed_users=[actor]),
        ContextProject(id="private", org_id=target.org_id, name="Secret project", repository_id="team/private", allowed_users=["other"]),
        ContextProject(id="foreign", org_id="foreign-org", name="Foreign", repository_id="team/foreign", allowed_users=[actor]),
    )
    assert client.get("/v1/context-projects").status_code == 401
    response = client.get("/v1/context-projects", headers=headers)
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"version": 1, "projects": [{"id": "vox", "name": "Vox", "repositoryId": "osborn-studio/osvox"}]}
    for project in ("private", "foreign", "missing"):
        denied = client.post("/v1/input-backups", headers=headers, json=body(projectId=project, repoSource="manual"))
        assert denied.status_code == 403
        assert denied.json() == {"detail": "PROJECT_DENIED"}
    assert store.count() == 0
    selected = body(projectId="vox", repoSource="manual")
    assert client.post("/v1/input-backups", headers=headers, json=selected).status_code == 202
    payload = json.loads(store.db.execute("SELECT raw_payload FROM backup_records").fetchone()[0])
    assert payload["repository"] is None
    assert payload["resolvedRepository"] == "osborn-studio/osvox"
    assert payload["projectId"] == "vox" and payload["repoSource"] == "manual"
    echo = {"version": 1, "projectId": "vox", "repositoryId": "osborn-studio/osvox"}
    receipt = client.get("/v1/input-backups/event-1", headers=headers)
    assert receipt.json()["attribution"] == echo
    assert "rawPayload" not in receipt.json()
    assert client.post("/v1/input-backups", headers=headers, json=selected).status_code == 202
    job = store.claim(now=10000000000)
    assert context.authorize_delivery(job)
    old_projects = context.projects
    context.projects = ()
    assert not context.authorize_delivery(job), "revoked project cannot deliver queued input"
    assert client.post("/v1/input-backups", headers=headers, json=selected).status_code == 202
    context.projects = old_projects
    # Operator changes do not silently rewrite an earlier frozen attribution.
    context.projects = (context.projects[0].model_copy(update={"repository_id": "team/renamed"}),)
    assert client.post("/v1/input-backups", headers=headers, json=selected).status_code == 202
    assert store.count() == 1
    assert not context.authorize_delivery(job), "changed repository cannot silently reroute a queued snapshot"
    assert json.loads(store.db.execute("SELECT raw_payload FROM backup_records").fetchone()[0]) == payload
    assert client.post("/v1/input-backups", headers=headers, json=selected).json()["attribution"] == echo
    assert client.get("/v1/input-backups/event-1", headers=headers).json()["attribution"] == echo


def test_project_fields_cannot_smuggle_local_paths_or_execution_context(setup):
    from engine.context_projects import ContextProject

    client, store, headers, target, context = setup
    context.projects = (ContextProject(id="vox", org_id=target.org_id, name="Vox", repository_id="team/vox", allowed_users=list(target.allowed_users)),)
    for extra in (
        {"projectId": "vox"},
        {"repoSource": "manual"},
        {"projectId": "vox", "repoSource": "ide"},
        {"projectId": "vox", "repoSource": "manual", "repository": "../../local"},
        {"projectId": "vox", "repoSource": "manual", "repository": "https://credential@example.test/repo"},
    ):
        assert client.post("/v1/input-backups", headers=headers, json=body(**extra)).status_code == 422
    assert store.count() == 0


def test_unselected_project_preserves_legacy_fingerprint(setup):
    import hashlib
    import json
    client, store, headers, _, _ = setup
    assert client.post("/v1/input-backups", headers=headers, json=body()).status_code == 202
    old_payload = {**body(), "repository": None}
    canonical = json.dumps(old_payload, ensure_ascii=False, sort_keys=True)
    row = store.db.execute("SELECT raw_payload, fingerprint FROM backup_records").fetchone()
    assert row["raw_payload"] == canonical
    assert row["fingerprint"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert client.post("/v1/input-backups", headers=headers, json=body()).status_code == 202


@pytest.mark.parametrize("repository", ["../repo", "owner/..", "/Users/name/repo", "https://token@host/repo", "git@host:owner/repo", "owner/repo/child"])
def test_project_repository_identifier_is_never_a_path(repository):
    from pydantic import ValidationError
    from engine.context_projects import ContextProject
    with pytest.raises(ValidationError):
        ContextProject(id="test", org_id="org", name="Test", repository_id=repository, allowed_users=["actor"])


def test_project_members_immutable_and_duplicates_fail_early():
    from pydantic import ValidationError
    from engine.context_projects import ContextProject
    from engine.backup_runtime import RuntimeSettings
    project = {"id": "vox", "org_id": "org", "name": "Vox", "repository_id": "team/vox", "allowed_users": ["actor"]}
    configured = ContextProject(**project)
    assert configured.allowed_users == ("actor",)
    with pytest.raises(ValidationError):
        configured.allowed_users = ("other",)
    with pytest.raises(ValidationError):
        ContextProject(**{**project, "allowed_users": ["actor", "actor"]})
    with pytest.raises(ValidationError, match="duplicate project configuration"):
        RuntimeSettings(data_dir="/tmp/unused-fixture", app_id="cli_fixture", app_secret_ref="unused", projects=[project, project])


@pytest.mark.parametrize("payload", ['broken', '[]', '{"projectId": true}', '{"projectId": "vox"}', '[' * 2000 + '0' + ']' * 2000])
def test_invalid_project_snapshot_blocks_only_its_job(setup, payload):
    client, store, headers, _, context = setup
    assert client.post("/v1/input-backups", headers=headers, json=body()).status_code == 202
    job = store.claim(now=10000000000)
    with store.db:
        store.db.execute("UPDATE backup_records SET raw_payload=? WHERE id=?", (payload, job["id"]))
    assert not context.authorize_delivery(job)
    assert context.enabled, "bad attribution must not disable entire service"


@pytest.mark.parametrize("damaged", ['broken', '[]', '{"projectId": "vox", "resolvedRepository": "/private/secret"}', '{"projectId": true, "resolvedRepository": "team/vox"}', '[' * 2000 + '0' + ']' * 2000])
def test_status_attribution_corruption_is_safe_and_not_echoed(setup, damaged):
    client, store, headers, _, _ = setup
    assert client.post("/v1/input-backups", headers=headers, json=body()).status_code == 202
    with store.db:
        store.db.execute("UPDATE backup_records SET raw_payload=?", (damaged,))
    response = client.get("/v1/input-backups/event-1", headers=headers)
    assert response.status_code == 200
    assert response.json()["attribution"] == {"version": 1, "valid": False}
    assert "secret" not in response.text
    assert "rawPayload" not in response.json()


def test_store_selected_project_requires_snapshot_but_replay_is_frozen(setup):
    _, store, _, _, _ = setup
    target = Target("archive", 1, "org", "oc_fixture", frozenset({"actor"}))
    value = BackupInput(**body(projectId="vox", repoSource="manual"))
    with pytest.raises(ValueError, match="invalid project snapshot"):
        store.accept("org", "actor", value, target)
    assert store.count() == 0
    first = store.accept("org", "actor", value, target, repository_snapshot="team/vox")
    replay = store.accept("org", "actor", value, target)
    assert first["attribution"] == replay["attribution"] == {"version": 1, "projectId": "vox", "repositoryId": "team/vox"}
    assert store.count() == 1
