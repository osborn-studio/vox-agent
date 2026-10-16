"""Offline restore rehearsals with synthetic identities and a no-network transport.

These tests exercise copied databases, never production stores or live Feishu.
Revocation must be performed explicitly before restored login service is exposed.
"""

import sqlite3

import pytest
from fastapi import HTTPException

from engine.feishu_login import FeishuLoginService
from engine.input_backup import BackupInput, BackupStore, Target
from engine.input_backup_worker import BackupWorker
from tests.test_feishu_login import service as service_fixture, login

service = service_fixture


def snapshot(connection, path):
    with sqlite3.connect(path) as destination:
        connection.backup(destination)


def test_restored_access_and_refresh_are_revoked_without_touching_original(service, tmp_path):
    original = login(service)
    path = tmp_path / "restored-login.sqlite"
    snapshot(service.db, path)
    restored = FeishuLoginService(
        service.settings,
        service.app_id,
        service.auth,
        path,
        service.provider,
        clock=service.clock,
    )
    try:
        # A copied pre-revocation session is live until the recovery procedure
        # explicitly revokes it. Do not put the recovered server on the network.
        assert restored.authenticate(original["token"]) is not None
        restored.revoke_user(original["profile"]["userId"])
        assert restored.authenticate(original["token"]) is None
        with pytest.raises(HTTPException) as error:
            restored.renew(original["refreshToken"])
        assert error.value.status_code == 401
        assert service.authenticate(original["token"]) is not None
    finally:
        restored.close()
    restarted = FeishuLoginService(
        service.settings,
        service.app_id,
        service.auth,
        path,
        service.provider,
        clock=service.clock,
    )
    try:
        assert restarted.authenticate(original["token"]) is None
        with pytest.raises(HTTPException):
            restarted.renew(original["refreshToken"])
    finally:
        restarted.close()


@pytest.mark.parametrize("status", ["delivered", "unknown", "sending", "pending"])
def test_copied_queue_never_replays_delivered_or_expired_ambiguous_jobs(tmp_path, status):
    original = BackupStore(tmp_path / "original.sqlite")
    target = Target("archive", 1, "org", "oc_synthetic", frozenset({"actor"}))
    value = BackupInput(eventId="synthetic-event", targetId="archive", text="synthetic")
    original.accept("org", "actor", value, target, now=1)
    if status != "pending":
        job = original.claim(now=2)
        if status != "sending":
            original.finish(
                job["id"],
                status,
                message_id="om_synthetic" if status == "delivered" else None,
                now=3,
            )
    path = tmp_path / "restored.sqlite"
    snapshot(original.db, path)
    restored = BackupStore(path)
    sends = []

    class Transport:
        def send(self, job):
            sends.append(job["id"])
            raise AssertionError("Recovery must not send")

    try:
        # Recovery starts with authorization withheld; pending work is quarantined.
        with BackupWorker(restored, Transport(), authorize=lambda _: False) as worker:
            worker.run_once()
        assert sends == []
        actual = restored.status("org", "actor", "synthetic-event")["status"]
        assert (
            actual
            == {
                "delivered": "delivered",
                "unknown": "unknown",
                "sending": "unknown",
                "pending": "blocked",
            }[status]
        )
        assert original.status("org", "actor", "synthetic-event")["status"] == status
        if status == "delivered":
            # Client replay after restore resolves to the retained record.
            assert restored.accept("org", "actor", value, target, now=100)["status"] == "delivered"
            assert restored.count() == 1
    finally:
        restored.close()
        original.close()

    # Quarantine and final states must survive a second process opening the
    # restored database. Re-enabling authorization alone must not replay them.
    reopened = BackupStore(path)
    try:
        before = reopened.status("org", "actor", "synthetic-event")
        with BackupWorker(reopened, Transport(), authorize=lambda _: True) as worker:
            worker.run_once()
        assert sends == []
        assert reopened.status("org", "actor", "synthetic-event") == before
        assert reopened.count() == 1
    finally:
        reopened.close()
