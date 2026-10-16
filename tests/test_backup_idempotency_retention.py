"""T03b: the backup idempotency record is durable and never deleted by service
code, so a client may safely resend the same eventId with the same payload
(ADR 2026-10-08-backup-idempotency-retention-v1)."""
import json
import re
from pathlib import Path

import pytest

from engine.input_backup import BackupConflict, BackupInput, BackupStore, Target

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs/decisions/2026-10-08-backup-idempotency-retention-v1.md"
ORG, ACTOR = "synthetic-org", "synthetic-actor"
TARGET = Target("team", 1, ORG, "oc_synthetic", frozenset({ACTOR}))


def request(**changes):
    return BackupInput.model_validate({"eventId": "event-1", "targetId": "team", "text": "synthetic", **changes})


def test_replay_returns_the_same_receipt_after_a_restart(tmp_path):
    first = BackupStore(tmp_path / "backup.sqlite")
    accepted = first.accept(ORG, ACTOR, request(), TARGET)
    first.close()

    reopened = BackupStore(tmp_path / "backup.sqlite")
    try:
        assert reopened.replay(ORG, ACTOR, request()) == accepted
        assert reopened.count() == 1
    finally:
        reopened.close()


def test_conflict_is_still_detected_after_a_restart(tmp_path):
    first = BackupStore(tmp_path / "backup.sqlite")
    first.accept(ORG, ACTOR, request(), TARGET)
    first.close()

    reopened = BackupStore(tmp_path / "backup.sqlite")
    try:
        with pytest.raises(BackupConflict):
            reopened.replay(ORG, ACTOR, request(text="different"))
    finally:
        reopened.close()


def test_terminal_records_remain_replayable(tmp_path):
    store = BackupStore(tmp_path / "backup.sqlite")
    try:
        store.accept(ORG, ACTOR, request(), TARGET)
        job = store.claim(now=1_800_000_000.0)
        store.finish(job["id"], "delivered", message_id="om_synthetic", now=1_800_000_000.0)
        replayed = store.replay(ORG, ACTOR, request())
        assert replayed["status"] == "delivered" and replayed["messageId"] == "om_synthetic"
    finally:
        store.close()


_DELETE = re.compile(
    r"DELETE\s+FROM\s+[\"'`]?(backup_records|backup_outbox)|DROP\s+TABLE\s+(IF\s+EXISTS\s+)?[\"'`]?backup_(records|outbox)"
    r"|TRUNCATE\s+[\"'`]?backup_",
    re.IGNORECASE,
)


_FILE_REMOVAL = re.compile(r"(unlink|remove|rmtree|rename|replace|truncate)\b|\brm\s+-")


def test_no_service_code_deletes_idempotency_records():
    offenders = []
    for folder in ("engine", "scripts", "deploy"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix not in {".py", ".sh", ".sql"} or not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            if _DELETE.search(text):
                offenders.append(str(path.relative_to(ROOT)))
            # A line that names the live backup database and removes or replaces a file.
            for line in text.splitlines():
                if "input-backup.sqlite" in line and ".worker.lock" not in line and _FILE_REMOVAL.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}: {line.strip()}")
    assert offenders == [], "retention change requires a new ADR and wire version: " + ", ".join(offenders)


def test_adr_exists_and_the_wire_fixture_points_to_it():
    assert ADR.is_file()
    text = ADR.read_text(encoding="utf-8")
    for required in ("(org, actor, eventId)", "never deleted", "wire", "pre_dispatch", "post_dispatch"):
        assert required in text, required
    fixture = json.loads((ROOT / "apps/desktop-tauri/test/fixtures/context-backup-wire-v2.json").read_text())
    assert ADR.stem in fixture["idempotency"]["retention"]
