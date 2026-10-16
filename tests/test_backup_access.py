import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from engine import backup_access as access
from engine.org_schema import ORG_DATABASE_NAME


@pytest.fixture
def config(tmp_path):
    identity = tmp_path / "data" / "identity"
    identity.mkdir(parents=True)
    with sqlite3.connect(identity / ORG_DATABASE_NAME) as db:
        db.execute("CREATE TABLE user (id TEXT, org_id TEXT, status TEXT)")
        db.executemany(
            "INSERT INTO user VALUES (?, ?, ?)",
            [
                ("alice", "team", "active"),
                ("bob", "team", "active"),
                ("eve", "other", "active"),
                ("disabled", "team", "suspended"),
            ],
        )
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path / "data"),
                "app_id": "cli_synthetic",
                "app_secret_ref": "SENTINEL_SECRET_REFERENCE",
                "targets": [
                    {
                        "id": "context",
                        "version": 1,
                        "org_id": "team",
                        "chat_id": "oc_synthetic",
                        "allowed_users": ["alice"],
                    }
                ],
            }
        )
    )
    path.chmod(0o640)
    return path


def run(path, action="grant", user="bob", **kw):
    return access.execute(path, action, "team", "context", user, **kw)


def test_preview_is_read_only_and_redacted(config):
    before = config.read_bytes()
    report = run(config)
    assert report["changed"] and not report["applied"]
    assert "SENTINEL" not in json.dumps(report)
    assert config.read_bytes() == before
    assert not config.with_name("runtime.json.access.lock").exists()


@pytest.mark.parametrize("user", ["unknown", "eve", "disabled"])
def test_no_inactive_or_cross_org_grants(config, user):
    with pytest.raises(access.AccessError, match="MEMBER_NOT_ACTIVE"):
        run(config, user=user)


def test_missing_target_and_invalid_identifier(config):
    with pytest.raises(access.AccessError, match="TARGET_NOT_UNIQUE"):
        access.execute(config, "grant", "other", "context", "alice")
    with pytest.raises(access.AccessError, match="INVALID_IDENTIFIER"):
        run(config, user="../alice")


def test_last_member_requires_explicit_target_disable(config):
    with pytest.raises(access.AccessError, match="LAST_MEMBER"):
        run(config, action="revoke", user="alice")
    assert run(config, action="disable-target", user=None)["after_member_count"] == 0
    with pytest.raises(access.AccessError, match="DOES_NOT_ACCEPT_USER"):
        run(config, action="disable-target", user="alice")


def test_tenant_wide_grant_cannot_masquerade_as_revoked(config):
    data = json.loads(config.read_text())
    data["feishu_login"] = {
        "public_base_url": "https://example.com",
        "tenant_orgs": {"tenant": "team"},
        "backup_target_ids": ["context"],
    }
    config.write_text(json.dumps(data))
    with pytest.raises(access.AccessError, match="TENANT_WIDE"):
        run(config)


def test_apply_snapshot_preserves_mode_and_unrelated_config(config, monkeypatch):
    monkeypatch.setattr(access, "require_stopped", lambda: None)
    before = config.read_bytes()
    report = run(config, apply=True)
    assert report["applied"] and report["activation"] == "restart_required"
    change = config.with_name("runtime.json.access-changes") / report["change_id"]
    assert (change / "before.json").read_bytes() == before
    assert (change / "after.json").read_bytes() == config.read_bytes()
    intent = json.loads((change / "intent.json").read_text())
    assert intent["admin_uid"] == os.geteuid()
    assert intent["after_sha256"] == access.digest(config.read_bytes())
    assert config.stat().st_mode & 0o777 == 0o640
    assert (change / "before.json").stat().st_mode & 0o777 == 0o600
    data = json.loads(config.read_text())
    assert data["app_secret_ref"] == "SENTINEL_SECRET_REFERENCE"
    assert data["targets"][0]["allowed_users"] == ["alice", "bob"]
    assert data["targets"][0]["version"] == 1
    assert not run(config, apply=True)["changed"]
    assert len(list(change.parent.iterdir())) == 1
    assert run(config, action="revoke", user="bob", apply=True)["applied"]
    assert json.loads(config.read_text())["targets"][0]["allowed_users"] == ["alice"]


def test_disable_target_applies_without_touching_other_settings(config, monkeypatch):
    monkeypatch.setattr(access, "require_stopped", lambda: None)
    assert run(config, action="disable-target", user=None, apply=True)["applied"]
    assert json.loads(config.read_text())["targets"] == []


@pytest.mark.parametrize(
    "stdout",
    [
        "LoadState=loaded\nActiveState=active\nMainPID=12",
        "LoadState=loaded\nActiveState=deactivating\nMainPID=0",
        "LoadState=not-found\nActiveState=inactive\nMainPID=0",
    ],
)
def test_running_or_missing_service_blocks_apply(config, monkeypatch, stdout):
    before = config.read_bytes()
    monkeypatch.setattr(access.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout))
    with pytest.raises(access.AccessError, match="STOP_VOX_TEAM"):
        run(config, apply=True)
    assert config.read_bytes() == before


def test_stopped_service_check(monkeypatch):
    monkeypatch.setattr(
        access.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="LoadState=loaded\nActiveState=inactive\nMainPID=0"),
    )
    access.require_stopped()


def test_atomic_replace_failure_keeps_original_and_intent(config, monkeypatch):
    monkeypatch.setattr(access, "require_stopped", lambda: None)
    before = config.read_bytes()
    monkeypatch.setattr(
        access.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError):
        run(config, apply=True)
    assert config.read_bytes() == before
    changes = list(config.with_name("runtime.json.access-changes").iterdir())
    assert len(changes) == 1
    assert (changes[0] / "before.json").read_bytes() == before
    assert not list(config.parent.glob(".access-*"))


def test_concurrent_manual_edit_is_preserved(config, monkeypatch):
    monkeypatch.setattr(access, "require_stopped", lambda: None)
    original = access.private_write

    def write(path, data):
        original(path, data)
        if path.name == "intent.json":
            config.write_text(config.read_text() + "\n")

    monkeypatch.setattr(access, "private_write", write)
    before = config.read_bytes()
    with pytest.raises(access.AccessError, match="CONFIG_CHANGED"):
        run(config, apply=True)
    assert config.read_bytes() == before + b"\n"


def test_config_symlink_and_fifo_are_rejected_without_blocking(config):
    link = config.with_name("link.json")
    link.symlink_to(config)
    with pytest.raises(OSError):
        run(link)
    fifo = config.with_name("fifo")
    os.mkfifo(fifo, 0o600)
    with pytest.raises(access.AccessError, match="UNSAFE_CONFIG"):
        run(fifo)


def test_world_readable_config_is_rejected(config):
    config.chmod(0o644)
    with pytest.raises(access.AccessError, match="UNSAFE_CONFIG"):
        run(config)


def test_missing_identity_database_not_created(config):
    database = config.parent / "data" / "identity" / ORG_DATABASE_NAME
    database.unlink()
    with pytest.raises(access.AccessError, match="IDENTITY_STORE_UNAVAILABLE"):
        run(config)
    assert not database.exists()


def test_runtime_denies_previously_queued_job_after_revocation(config, monkeypatch, request):
    from engine.backup_app import BackupAppContext
    from engine.auth import Edition
    from engine.input_backup import BackupInput, BackupStore, Target

    monkeypatch.setattr(access, "require_stopped", lambda: None)
    auth = SimpleNamespace(
        edition=Edition.ENTERPRISE,
        org_store=SimpleNamespace(get_user=lambda actor: {"org_id": "team", "status": "active"}),
    )
    store = BackupStore(config.parent / "synthetic-revocation-queue.sqlite")
    request.addfinalizer(store.close)
    queued_target = Target("context", 1, "team", "oc_synthetic", frozenset({"bob"}))
    store.accept("team", "bob", BackupInput(
        eventId="synthetic-queued-before-revocation", targetId="context", text="synthetic",
    ), queued_target, now=1)
    job = store.claim(now=2)
    assert job is not None

    def context():
        _, _, settings, _ = access.read_config(config)
        targets = tuple(
            Target(t.id, t.version, t.org_id, t.chat_id, frozenset(t.allowed_users))
            for t in settings.targets
        )
        return BackupAppContext(auth_service=auth, backup_store=store, targets=targets, enabled=True)

    assert not context().authorize_delivery(job)
    run(config, apply=True)
    assert context().authorize_delivery(job)
    run(config, action="revoke", user="bob", apply=True)
    assert not context().authorize_delivery(job)


def test_cli_list_excludes_secret_references(config, monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["backup_access", "--config", str(config), "list"])
    assert access.main() == 0
    output = capsys.readouterr().out
    assert "SENTINEL" not in output
    assert json.loads(output)["targets"][0]["allowed_users"] == ["alice"]


def test_cli_validation_error_does_not_print_config(config, monkeypatch, capsys):
    import sys

    config.write_text('{"unexpected_secret":"SENTINEL"}')
    monkeypatch.setattr(sys, "argv", ["backup_access", "--config", str(config), "list"])
    assert access.main() == 1
    assert "SENTINEL" not in capsys.readouterr().out


def test_parallel_apply_refuses_lock(config, monkeypatch):
    import fcntl

    monkeypatch.setattr(access, "require_stopped", lambda: None)
    lock = os.open(config.with_name("runtime.json.access.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    before = config.read_bytes()
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            run(config, apply=True)
    finally:
        os.close(lock)
    assert config.read_bytes() == before


def test_service_restart_before_replace_blocks_apply(config, monkeypatch):
    checks = []

    def check():
        checks.append(True)
        if len(checks) == 2:
            raise access.AccessError("STOP_VOX_TEAM_BEFORE_APPLY")

    monkeypatch.setattr(access, "require_stopped", check)
    before = config.read_bytes()
    with pytest.raises(access.AccessError, match="STOP_VOX_TEAM"):
        run(config, apply=True)
    assert config.read_bytes() == before


def test_post_replace_failure_retains_recovery_intent(config, monkeypatch):
    monkeypatch.setattr(access, "require_stopped", lambda: None)
    original = access.fsync_dir
    parent_calls = []

    def fsync(path):
        if path == config.parent:
            parent_calls.append(True)
            if len(parent_calls) == 2:
                raise OSError("injected fsync failure after replace")
        original(path)

    monkeypatch.setattr(access, "fsync_dir", fsync)
    with pytest.raises(OSError):
        run(config, apply=True)
    assert "bob" in json.loads(config.read_text())["targets"][0]["allowed_users"]
    change = next(config.with_name("runtime.json.access-changes").iterdir())
    assert json.loads((change / "intent.json").read_text())["after_sha256"] == access.digest(
        config.read_bytes()
    )
