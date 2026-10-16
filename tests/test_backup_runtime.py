"""Runtime integration, using only fake transport and synthetic credentials."""

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from engine.backup_runtime import RuntimeSettings, create_runtime_app


def settings(tmp_path, **updates):
    return RuntimeSettings.model_validate(
        {
            "data_dir": str(tmp_path),
            "app_id": "cli_test",
            "bootstrap_secret_ref": "bootstrap",
            "app_secret_ref": "feishu",
            "targets": [
                {
                    "id": "archive",
                    "version": 1,
                    "org_id": "default-org",
                    "chat_id": "oc_test",
                    "allowed_users": ["bootstrap-admin"],
                }
            ],
            **updates,
        }
    )


def secret(reference):
    return "test-bootstrap-credential-long-enough" if reference == "bootstrap" else "test-secret"


def test_disabled_runtime_never_reads_feishu_secret(tmp_path):
    calls = []

    def reader(ref):
        calls.append(ref)
        return secret(ref)

    app = create_runtime_app(settings(tmp_path), read_secret=reader)
    with TestClient(app) as client:
        assert client.get("/healthz").json()["enabled"] is False
        assert calls == ["bootstrap"]
    assert not app.state.runtime.worker_alive


def test_lifespan_worker_delivers_and_stops(tmp_path):
    class FakeTransport:
        def send(self, job):
            return "delivered", "om_test"

    app = create_runtime_app(
        settings(tmp_path, enabled=True),
        read_secret=secret,
        transport_factory=lambda client, reader: FakeTransport(),
    )
    headers = {"Authorization": "Bearer test-bootstrap-credential-long-enough"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/input-backups",
                headers=headers,
                json={"eventId": "event-test", "targetId": "archive", "text": "synthetic test"},
            ).status_code
            == 202
        )
        for _ in range(100):
            response = client.get("/v1/input-backups/event-test", headers=headers)
            if response.json()["status"] == "delivered":
                break
            time.sleep(0.02)
        assert response.json()["status"] == "delivered"
        assert app.state.runtime.worker_alive
    assert not app.state.runtime.worker_alive


def test_rejects_secrets_in_configuration(tmp_path):
    with pytest.raises(ValidationError):
        settings(tmp_path, app_secret="do-not-store-here")
    with pytest.raises(ValidationError):
        settings(tmp_path, enabled="true")


def test_worker_start_failure_closes_store_and_releases_lock(tmp_path):
    from engine.input_backup import BackupStore
    from engine.input_backup_worker import BackupWorker

    store = BackupStore(tmp_path / "input-backup.sqlite")
    with BackupWorker(store, None, authorize=lambda job: True):
        app = create_runtime_app(
            settings(tmp_path, enabled=True),
            read_secret=secret,
            transport_factory=lambda client, reader: None,
        )
        with pytest.raises(RuntimeError, match="BACKUP_WORKER_START_FAILED"):
            with TestClient(app):
                pass
        assert not app.state.runtime.worker_alive
    store.close()
    # The failed startup didn't leak an owning worker handle.
    with TestClient(
        create_runtime_app(
            settings(tmp_path, enabled=True),
            read_secret=secret,
            transport_factory=lambda client, reader: None,
        )
    ) as client:
        assert client.get("/healthz").status_code == 200


def test_missing_secret_fails_before_worker_starts(tmp_path):
    def unavailable(ref):
        if ref == "bootstrap":
            return secret(ref)
        raise RuntimeError("BACKUP_SECRET_UNAVAILABLE")

    app = create_runtime_app(settings(tmp_path, enabled=True), read_secret=unavailable)
    with pytest.raises(RuntimeError, match="BACKUP_SECRET_UNAVAILABLE"):
        with TestClient(app):
            pass
    assert not app.state.runtime.worker_alive


def test_worker_failure_disables_ingest_and_health(tmp_path):
    app = create_runtime_app(
        settings(tmp_path, enabled=True),
        read_secret=secret,
        transport_factory=lambda client, reader: None,
    )
    with TestClient(app) as client:

        def fail():
            raise RuntimeError("private details must not appear")

        app.state.context.backup_store.claim = fail
        assert app.state.runtime.failed.wait(2)
        response = client.get("/healthz")
        assert response.status_code == 503
        assert "private details" not in response.text
        headers = {"Authorization": "Bearer test-bootstrap-credential-long-enough"}
        assert (
            client.post(
                "/v1/input-backups",
                headers=headers,
                json={"eventId": "after-failure", "targetId": "archive", "text": "test"},
            ).status_code
            == 503
        )


def test_rejects_public_data_directory(tmp_path):
    root = tmp_path / "public"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(RuntimeError, match="PRIVATE"):
        with TestClient(create_runtime_app(settings(root), read_secret=secret)):
            pass


def test_bootstrap_secret_only_read_on_first_initialization(tmp_path):
    with TestClient(create_runtime_app(settings(tmp_path), read_secret=secret)):
        pass

    def never_read(ref):
        pytest.fail("existing identity must not reload bootstrap secret")

    with TestClient(create_runtime_app(settings(tmp_path), read_secret=never_read)) as client:
        assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("backup", [False, True])
@pytest.mark.parametrize("conversations", [False, True])
def test_feature_switches_are_independent(tmp_path, backup, conversations):
    class FakeTransport:
        def send(self, job):
            return "delivered", "om_test"

    calls = []

    def reader(ref):
        calls.append(ref)
        return secret(ref)

    app = create_runtime_app(
        settings(
            tmp_path,
            enabled=backup,
            conversations_enabled=conversations,
            feishu_login={
                "public_base_url": "http://127.0.0.1:8766",
                "tenant_orgs": {"team": "default-org"},
            },
        ),
        read_secret=reader,
        transport_factory=lambda *_: FakeTransport(),
    )
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        assert app.state.context.conversations_enabled is conversations
        assert app.state.context.enabled is backup
        assert app.state.runtime.worker_alive is backup
        assert ("feishu" in calls) is backup
        response = client.post(
            "/v1/input-backups",
            headers={
                "Authorization": "Bearer " + secret("bootstrap"),
            },
            json={"eventId": "independent", "targetId": "archive", "text": "synthetic"},
        )
        assert response.status_code == (202 if backup else 503)
        assert client.get("/v1/conversations").status_code == (401 if conversations else 404)


def policy():
    return {
        "pinned_chat_ids": ["oc_vox"],
        "denied_chat_ids": ["oc_denied"],
        "shared_read_chat_ids": [
            {
                "chat_id": "oc_vox",
                "created_by": "admin-test",
                "effective_at": "2026-09-27T00:00:00Z",
                "version": 1,
                "audit_ref": "approval-test",
                "scope": "current_members",
            }
        ],
    }


def test_conversation_defaults_and_policy_reach_context(tmp_path):
    assert settings(tmp_path).conversations_enabled is False
    configured = settings(tmp_path, conversation_policy=policy())
    with TestClient(create_runtime_app(configured, read_secret=secret)) as client:
        assert client.app.state.context.conversation_policy.shared_read_chat_ids[0].version == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"unknown": True},
        {"pinned_chat_ids": ["bad"]},
        {"pinned_chat_ids": [True]},
        {"denied_chat_ids": "oc_no"},
        {"pinned_chat_ids": ["oc_same", "oc_same"]},
        {"shared_read_chat_ids": ["oc_vox"]},
        {"pinned_chat_ids": ["oc_same"], "denied_chat_ids": ["oc_same"]},
    ],
)
def test_invalid_conversation_policy_rejected(tmp_path, invalid):
    with pytest.raises(ValidationError):
        settings(tmp_path, conversation_policy=invalid)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", "1"),
        ("version", 0),
        ("created_by", " "),
        ("audit_ref", ""),
        ("effective_at", "2026-09-27"),
        ("effective_at", "2026-09-27T00:00:00"),
        ("effective_at", 1),
        ("revoked_at", "2026-09-26T00:00:00Z"),
        ("scope", "all_history"),
        ("unknown", True),
        ("chat_id", "oc_bad/id"),
    ],
)
def test_invalid_shared_policy_rejected(tmp_path, field, value):
    value_policy = policy()
    value_policy["shared_read_chat_ids"][0][field] = value
    with pytest.raises(ValidationError):
        settings(tmp_path, conversation_policy=value_policy)


def test_conversations_require_single_tenant_login(tmp_path):
    for updates in [
        {"conversations_enabled": "true"},
        {"conversations_enabled": True},
        {
            "conversations_enabled": True,
            "feishu_login": {
                "public_base_url": "http://127.0.0.1:8766",
                "tenant_orgs": {"one": "default-org", "two": "default-org"},
            },
        },
    ]:
        with pytest.raises(ValidationError):
            settings(tmp_path, **updates)


def test_cli_proxy_configuration_ignores_spoofed_addresses(tmp_path, monkeypatch):
    import json
    import sys

    import uvicorn

    from engine import backup_runtime

    config = settings(
        tmp_path,
        feishu_login={
            "public_base_url": "http://127.0.0.1:8766",
            "tenant_orgs": {"team": "default-org"},
        },
    )
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(config.model_dump()))
    factory = create_runtime_app
    monkeypatch.setattr(
        backup_runtime, "create_runtime_app", lambda settings: factory(settings, read_secret=secret)
    )
    monkeypatch.setattr(sys, "argv", ["backup_runtime", "--config", str(path)])

    def run(app, **kwargs):
        assert kwargs["workers"] == 1
        assert kwargs["proxy_headers"] is False
        assert kwargs["access_log"] is False
        server = uvicorn.Config(app, **kwargs)
        server.load()
        # Exercise the real Uvicorn middleware stack without opening a socket.
        with TestClient(server.loaded_app, base_url="http://127.0.0.1:8766") as client:
            for i in range(6):
                response = client.post(
                    "/v1/auth/feishu/begin",
                    headers={
                        "X-Forwarded-For": f"198.51.100.{i + 1}",
                        "Forwarded": f"for=198.51.100.{i + 1};proto=https",
                    },
                )
                assert response.status_code == (200 if i < 5 else 429)

    monkeypatch.setattr(uvicorn, "run", run)
    backup_runtime.main()


def test_policy_revocation_and_conflict_validation(tmp_path):
    value = policy()
    value["shared_read_chat_ids"][0]["revoked_at"] = "2026-09-28T00:00:00+00:00"
    assert (
        settings(tmp_path, conversation_policy=value)
        .conversation_policy.shared_read_chat_ids[0]
        .revoked_at
    )
    value["shared_read_chat_ids"].append(value["shared_read_chat_ids"][0].copy())
    with pytest.raises(ValidationError):
        settings(tmp_path, conversation_policy=value)
    value = policy()
    value["denied_chat_ids"] = ["oc_vox"]
    with pytest.raises(ValidationError):
        settings(tmp_path, conversation_policy=value)


def test_switch_changes_preserve_backup_queue(tmp_path):
    from engine.input_backup import DeliveryPaused

    app = create_runtime_app(settings(tmp_path), read_secret=secret)
    headers = {"Authorization": "Bearer " + secret("bootstrap")}
    with TestClient(app) as client:
        context = app.state.context
        context.enabled = True
        assert (
            client.post(
                "/v1/input-backups",
                headers=headers,
                json={
                    "eventId": "queued",
                    "targetId": "archive",
                    "text": "synthetic",
                },
            ).status_code
            == 202
        )
        job = context.backup_store.claim()
        assert context.authorize_delivery(job)
        context.conversations_enabled = True
        assert context.authorize_delivery(job)
        context.enabled = False
        with pytest.raises(DeliveryPaused):
            context.authorize_delivery(job)
        context.conversations_enabled = False
        assert client.get("/v1/input-backups/queued", headers=headers).status_code == 200
        assert job["chat"] == "oc_test" and job["target"] == "archive"


@pytest.mark.parametrize("value", ["admin\nforged", "admin\x00", " admin", "admin "])
def test_policy_audit_identifiers_are_printable_and_unambiguous(tmp_path, value):
    for field in ("created_by", "audit_ref"):
        candidate = policy()
        candidate["shared_read_chat_ids"][0][field] = value
        with pytest.raises(ValidationError):
            settings(tmp_path, conversation_policy=candidate)


def test_private_context_runtime_optin_without_delivery_or_feishu_secret(tmp_path):
    calls = []

    def reader(reference):
        calls.append(reference)
        return secret(reference)

    app = create_runtime_app(settings(tmp_path, private_context_enabled=True),read_secret=reader)
    with TestClient(app) as client:
        auth = app.state.context.auth_service
        user = auth.create_user(org_id='default-org',email='private@example.test',display_name='Private')
        _, token = auth.issue_credential(user_id=user['id'],name='synthetic')
        result = client.post('/v1/context-private',headers={'Authorization':'Bearer '+token},
                             json={'eventId':'private-runtime','text':'personal intent'})
        assert result.status_code == 200 and result.json()['visibility'] == 'private'
        assert calls == ['bootstrap']
        assert app.state.context.backup_store.count() == 0
        assert client.get('/healthz').json()['private_context_enabled'] is True
    assert app.state.context is None and not app.state.runtime.worker_alive


def test_private_context_runtime_default_off_no_routes(tmp_path):
    app = create_runtime_app(settings(tmp_path),read_secret=secret)
    with TestClient(app) as client:
        assert client.post('/v1/context-private',json={}).status_code == 404
        assert not app.state.context.backup_store.db.execute("SELECT 1 FROM sqlite_master WHERE name='private_context_v1'").fetchone()


def test_private_migration_failure_refuses_runtime_start(tmp_path, monkeypatch):
    from engine.context_private import PrivateContextJournal

    def broken(self, path):
        raise RuntimeError('synthetic migration failure')

    monkeypatch.setattr(PrivateContextJournal,'__init__',broken)
    app = create_runtime_app(settings(tmp_path,private_context_enabled=True),read_secret=secret)
    with pytest.raises(RuntimeError,match='synthetic migration'):
        with TestClient(app):
            pytest.fail('migration failure must not publish a healthy server')
    assert not app.state.runtime.worker_alive
