"""T03d: operator recovery for blocked and out-of-window unknown deliveries.

Synthetic local databases only. No Feishu calls.
"""
import fcntl
import json
from pathlib import Path

import pytest

from engine.backup_delivery_recovery import (
    MAX_EVENTS,
    RecoveryError,
    apply_recovery,
    plan_recovery,
)
from engine.input_backup import RETRY_WINDOW_SECONDS, BackupInput, BackupStore, Target

ORG, ACTOR, OTHER = "org-1", "actor-1", "actor-2"
TARGET = Target("team", 1, ORG, "oc_synthetic", frozenset({ACTOR, OTHER}))
NOW = 1_800_000_000.0
SECRET_TEXT = "synthetic secret body"


def submit(store, event, actor=ACTOR):
    store.accept(ORG, actor, BackupInput.model_validate(
        {"eventId": event, "targetId": "team", "text": SECRET_TEXT}), TARGET)
    with store.lock, store.db:
        store.db.execute(
            "UPDATE backup_outbox SET next_attempt=0 WHERE id=(SELECT id FROM backup_records WHERE event=? AND actor=?)",
            (event, actor))


def drive(store, event, final, *, actor=ACTOR, at=NOW):
    """Claim exactly this event and end it the way the worker does: `blocked`
    through store.block (authorization denied, nothing sent), others via finish."""
    record_id = store.db.execute("SELECT id FROM backup_records WHERE event=? AND actor=?", (event, actor)).fetchone()[0]
    with store.lock, store.db:
        store.db.execute("UPDATE backup_outbox SET next_attempt=? WHERE id<>?", (NOW * 10, record_id))
        store.db.execute("UPDATE backup_outbox SET next_attempt=0 WHERE id=?", (record_id,))
    job = store.claim(now=at)
    assert job and job["id"] == record_id
    if final == "blocked":
        store.block(job, now=at)
    else:
        store.finish(record_id, final, now=at)
    with store.lock, store.db:
        store.db.execute("UPDATE backup_outbox SET next_attempt=0 WHERE id<>? AND status IN ('pending','unknown')", (record_id,))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "input-backup.sqlite"
    store = BackupStore(path)
    submit(store, "blocked-1")
    drive(store, "blocked-1", "blocked")
    submit(store, "unknown-old")
    drive(store, "unknown-old", "unknown")
    submit(store, "unknown-new")
    drive(store, "unknown-new", "unknown", at=NOW + RETRY_WINDOW_SECONDS)
    submit(store, "delivered-1")
    drive(store, "delivered-1", "delivered")
    submit(store, "pending-1")
    submit(store, "blocked-1", actor=OTHER)
    drive(store, "blocked-1", "blocked", actor=OTHER)
    store.close()
    return path


def status(path, event, actor=ACTOR):
    store = BackupStore(path)
    try:
        return store.status(ORG, actor, event)["status"]
    finally:
        store.close()


def later():
    return NOW + RETRY_WINDOW_SECONDS + 10


def test_preview_classifies_without_changing_anything(db):
    plan = plan_recovery(db, ORG, ACTOR, ["blocked-1", "unknown-old", "unknown-new", "delivered-1", "pending-1", "missing"], now=later())
    by_event = {item["eventId"]: item for item in plan["items"]}
    assert by_event["blocked-1"]["eligible"] is True and by_event["blocked-1"]["requires_attestation"] is False
    assert by_event["unknown-old"]["eligible"] is True and by_event["unknown-old"]["requires_attestation"] is True
    assert by_event["unknown-new"]["eligible"] is False and by_event["unknown-new"]["reason"] == "WORKER_STILL_RETRYING"
    assert by_event["delivered-1"]["reason"] == "NOT_RECOVERABLE_STATUS"
    assert by_event["pending-1"]["reason"] == "NOT_RECOVERABLE_STATUS"
    assert by_event["missing"]["reason"] == "NOT_FOUND"
    assert status(db, "blocked-1") == "blocked"
    assert SECRET_TEXT not in json.dumps(plan)


def test_blocked_is_requeued_with_an_actor_bearing_audit_row(db):
    result = apply_recovery(db, ORG, ACTOR, ["blocked-1"], operator="ops-alice", reason="target membership restored",
                            now=later())
    assert result["requeued"] == ["blocked-1"]
    assert status(db, "blocked-1") == "pending"
    assert status(db, "blocked-1", actor=OTHER) == "blocked"  # other actor's same eventId untouched
    store = BackupStore(db)
    try:
        audit = [dict(row) for row in store.db.execute("SELECT * FROM backup_delivery_recovery_audit")]
        job = store.claim(now=later())
    finally:
        store.close()
    assert len(audit) == 1
    assert audit[0]["operator"] == "ops-alice" and audit[0]["from_status"] == "blocked"
    assert audit[0]["attested_not_delivered"] == 0 and audit[0]["os_uid"] is not None
    assert SECRET_TEXT not in json.dumps(audit)
    assert job is not None  # the worker picks the record up again


def test_unknown_requires_attestation_that_the_message_is_absent(db):
    with pytest.raises(RecoveryError, match="ATTESTATION_REQUIRED"):
        apply_recovery(db, ORG, ACTOR, ["unknown-old"], operator="ops-alice", reason="checked group", now=later())
    assert status(db, "unknown-old") == "unknown"
    result = apply_recovery(db, ORG, ACTOR, ["unknown-old"], operator="ops-alice", reason="checked group",
                            attest_not_delivered=True, now=later())
    assert result["requeued"] == ["unknown-old"]
    assert status(db, "unknown-old") == "pending"


def test_ineligible_records_are_never_changed(db):
    result = apply_recovery(db, ORG, ACTOR, ["unknown-new", "delivered-1", "pending-1", "missing"],
                            operator="ops-alice", reason="sweep", attest_not_delivered=True, now=later())
    assert result["requeued"] == []
    assert status(db, "unknown-new") == "unknown" and status(db, "delivered-1") == "delivered"


def test_rerun_is_an_idempotent_no_op(db):
    apply_recovery(db, ORG, ACTOR, ["blocked-1"], operator="ops-alice", reason="restored", now=later())
    again = apply_recovery(db, ORG, ACTOR, ["blocked-1"], operator="ops-alice", reason="restored", now=later())
    assert again["requeued"] == []
    store = BackupStore(db)
    try:
        assert store.db.execute("SELECT count(*) FROM backup_delivery_recovery_audit").fetchone()[0] == 1
    finally:
        store.close()


def test_apply_refuses_while_the_worker_holds_its_lock(db):
    with open(str(db) + ".worker.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RecoveryError, match="WORKER_RUNNING"):
            apply_recovery(db, ORG, ACTOR, ["blocked-1"], operator="ops-alice", reason="restored", now=later())
    assert status(db, "blocked-1") == "blocked"


def test_status_change_between_plan_and_apply_is_skipped(db, monkeypatch):
    import engine.backup_delivery_recovery as recovery

    real_plan = recovery.plan_recovery

    def racing_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        store = BackupStore(db)
        with store.lock, store.db:
            store.db.execute("UPDATE backup_outbox SET status='delivered' WHERE id=(SELECT id FROM backup_records WHERE event='blocked-1' AND actor=?)", (ACTOR,))
        store.close()
        return plan

    monkeypatch.setattr(recovery, "plan_recovery", racing_plan)
    result = apply_recovery(db, ORG, ACTOR, ["blocked-1"], operator="ops-alice", reason="restored", now=later())
    assert result["requeued"] == [] and result["skipped_changed"] == ["blocked-1"]
    assert status(db, "blocked-1") == "delivered"


@pytest.mark.parametrize("operator,reason,error", [
    ("", "ok", "OPERATOR_REQUIRED"),
    ("ops\nalice", "ok", "OPERATOR_REQUIRED"),
    ("ops-alice", "", "REASON_REQUIRED"),
    ("ops-alice", "x" * 501, "REASON_REQUIRED"),
    ("ops-alice", "bad\x00reason", "REASON_REQUIRED"),
])
def test_operator_and_reason_are_required_and_bounded(db, operator, reason, error):
    with pytest.raises(RecoveryError, match=error):
        apply_recovery(db, ORG, ACTOR, ["blocked-1"], operator=operator, reason=reason, now=later())


def test_selection_is_bounded_and_explicit(db):
    with pytest.raises(RecoveryError, match="TOO_MANY_EVENTS"):
        plan_recovery(db, ORG, ACTOR, [f"e{i}" for i in range(MAX_EVENTS + 1)], now=later())
    with pytest.raises(RecoveryError, match="NO_EVENTS"):
        plan_recovery(db, ORG, ACTOR, [], now=later())
    with pytest.raises(RecoveryError, match="INVALID_EVENT_ID"):
        plan_recovery(db, ORG, ACTOR, ["bad id"], now=later())


def test_blocked_after_a_possible_post_requires_attestation(tmp_path):
    """unknown (post may have arrived) -> retry -> authorization denied -> blocked."""
    path = tmp_path / "input-backup.sqlite"
    store = BackupStore(path)
    submit(store, "maybe-sent")
    drive(store, "maybe-sent", "unknown")
    drive(store, "maybe-sent", "blocked", at=NOW + 61)
    store.close()
    plan = plan_recovery(path, ORG, ACTOR, ["maybe-sent"], now=later())
    assert plan["items"][0]["eligible"] is True and plan["items"][0]["requires_attestation"] is True
    with pytest.raises(RecoveryError, match="ATTESTATION_REQUIRED"):
        apply_recovery(path, ORG, ACTOR, ["maybe-sent"], operator="ops-alice", reason="access fixed", now=later())


def test_real_worker_preserves_post_history_when_authorization_is_denied(tmp_path):
    from engine.input_backup_worker import BackupWorker

    class Transport:
        def send(self, job):
            raise RuntimeError("lost response")

    path = tmp_path / "input-backup.sqlite"
    store = BackupStore(path)
    submit(store, "worker-1")
    allowed = [True]
    with BackupWorker(store, Transport(), authorize=lambda job: allowed[0]) as worker:
        worker.run_once()  # posts, response lost -> unknown
        with store.lock, store.db:
            store.db.execute("UPDATE backup_outbox SET next_attempt=0")
        allowed[0] = False
        worker.run_once()  # retry denied -> blocked, but a post may have reached Feishu
    store.close()
    item = plan_recovery(path, ORG, ACTOR, ["worker-1"], now=later())["items"][0]
    assert item["status"] == "blocked" and item["requires_attestation"] is True


def test_blocked_that_was_never_posted_needs_no_attestation(db):
    item = plan_recovery(db, ORG, ACTOR, ["blocked-1"], now=later())["items"][0]
    assert item["requires_attestation"] is False


def test_symlinked_database_path_still_honours_the_worker_lock(db, tmp_path):
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(db)
    with open(str(db.resolve()) + ".worker.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RecoveryError, match="WORKER_RUNNING"):
            apply_recovery(alias, ORG, ACTOR, ["blocked-1"], operator="ops-alice", reason="restored", now=later())


def test_preview_is_read_only_and_never_creates_a_database(db, tmp_path):
    before = (db.stat().st_mtime_ns, db.stat().st_mode, sorted(p.name for p in db.parent.iterdir()))
    plan_recovery(db, ORG, ACTOR, ["blocked-1"], now=later())
    assert (db.stat().st_mtime_ns, db.stat().st_mode, sorted(p.name for p in db.parent.iterdir())) == before
    missing = tmp_path / "typo.sqlite"
    with pytest.raises(RecoveryError, match="DATABASE_NOT_FOUND"):
        plan_recovery(missing, ORG, ACTOR, ["blocked-1"], now=later())
    assert not missing.exists()


def test_preview_refuses_an_uncheckpointed_wal_without_shared_memory(db):
    wal = Path(str(db) + "-wal")
    wal.write_bytes(b"")
    try:
        with pytest.raises(RecoveryError, match="DATABASE_IN_USE"):
            plan_recovery(db, ORG, ACTOR, ["blocked-1"], now=later())
        assert not Path(str(db) + "-shm").exists()
    finally:
        wal.unlink()


def test_cli_previews_by_default_and_never_prints_message_text(db, capsys):
    from engine.backup_delivery_recovery import main

    code = main(["--db", str(db), "--org", ORG, "--actor", ACTOR, "--event", "blocked-1"])
    out = capsys.readouterr().out
    assert code == 0 and '"mode": "preview"' in out and SECRET_TEXT not in out
    assert status(db, "blocked-1") == "blocked"
