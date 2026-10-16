from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "team_status", Path(__file__).parents[1] / "deploy/feishu-team/status.py"
)
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)
NOW = 1791090000


def test_health_does_not_echo_body_or_error():
    assert (
        status.check_health(lambda *a, **k: io.BytesIO(b'{"status":"ok","enabled":true}'))["status"]
        == "ok"
    )
    assert (
        status.check_health(lambda *a, **k: io.BytesIO(b'{"status":"ok","enabled":false}'))[
            "status"
        ]
        == "attention"
    )
    result = status.check_health(lambda *a, **k: io.BytesIO(b"SECRET" * 2000))
    assert result == status.unknown("https_delivery")


@pytest.mark.parametrize(
    "free,total,expected",
    [
        (11 * 1024**3, 40 * 1024**3, "ok"),
        (9 * 1024**3, 40 * 1024**3, "attention"),
        (15 * 1024**3, 100 * 1024**3, "attention"),
        (0, 0, "unknown"),
    ],
)
def test_disk_thresholds(free, total, expected):
    assert status.check_disk(disk_usage=lambda p: (total, total - free, free))["status"] == expected


def test_unit_missing_failed_stale_and_future():
    properties = {
        "LoadState": "loaded",
        "ActiveState": "inactive",
        "Result": "success",
        "ExecMainStatus": "0",
        "ExecMainExitTimestamp": "Sun 2026-10-04 05:00:00 UTC",
    }
    stamp = status.datetime(2026, 10, 4, 5, tzinfo=status.timezone.utc).timestamp()
    assert status.check_unit("cos_last_run", properties, stamp + 60)["status"] == "ok"
    assert status.check_unit("cos_last_run", properties, stamp + 27 * 3600)["status"] == "attention"
    assert status.check_unit("cos_last_run", properties, stamp - 120)["status"] == "unknown"
    assert (
        status.check_unit("cos_last_run", {**properties, "Result": "exit-code"}, stamp)["status"]
        == "attention"
    )
    assert status.check_unit("cos_last_run", {}, stamp)["status"] == "unknown"
    assert (
        status.check_unit(
            "service", {"LoadState": "loaded", "ActiveState": "active", "MainPID": "12"}, stamp
        )["status"]
        == "ok"
    )
    assert (
        status.check_unit(
            "service", {"LoadState": "loaded", "ActiveState": "active", "MainPID": "0"}, stamp
        )["status"]
        == "attention"
    )


def test_systemd_query_is_read_only_and_timezone_explicit():
    def run(command, **kwargs):
        assert command[:2] == ["systemctl", "show"]
        assert kwargs["env"]["TZ"] == "UTC"
        return SimpleNamespace(
            stdout="Id=vox-team.service\nLoadState=loaded\n\nId=vox-team-cos.service\nResult=success\n"
        )

    assert len(status.unit_properties(run)) == 2


def test_snapshot_uses_filename_not_recent_copy_mtime(tmp_path):
    p = tmp_path / "vox-20200101T000000Z.tar.gpg"
    p.write_bytes(b"encrypted-test-data")
    assert status.check_snapshot(tmp_path, NOW)["status"] == "attention"
    p.unlink()
    assert status.check_snapshot(tmp_path, NOW)["count"] == 0
    p.symlink_to("/does/not/exist")
    assert status.check_snapshot(tmp_path, NOW)["status"] == "unknown"


def test_fresh_and_invalid_snapshot(tmp_path):
    date = status.datetime.fromtimestamp(NOW, status.timezone.utc).strftime(
        "vox-%Y%m%dT%H%M%SZ.tar.gpg"
    )
    p = tmp_path / date
    p.write_bytes(b"encrypted-test-data")
    assert status.check_snapshot(tmp_path, NOW)["status"] == "ok"
    p.write_bytes(b"tiny")
    assert status.check_snapshot(tmp_path, NOW)["status"] == "unknown"


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "input-backup.sqlite"
    with sqlite3.connect(p) as db:
        db.executescript(
            "CREATE TABLE backup_records(id TEXT PRIMARY KEY, created REAL, raw_payload TEXT); CREATE TABLE backup_outbox(id TEXT,status TEXT);"
        )
        db.execute(
            "INSERT INTO backup_records VALUES (?,?,?)", ("1", NOW - 30, "PRIVATE_EMPLOYEE_TEXT")
        )
        db.execute("INSERT INTO backup_outbox VALUES (?,?)", ("1", "pending"))
    return p


def test_queue_aggregates_only_and_preserves_content(db_path):
    before = db_path.read_bytes()
    result = status.check_queue(db_path, NOW)
    assert result["status"] == "ok"
    assert result["counts"]["pending"] == 1
    assert result["oldest_wait_seconds"] == 30
    assert "PRIVATE" not in json.dumps(result)
    assert db_path.read_bytes() == before


@pytest.mark.parametrize("state", ["blocked", "unknown", "pending", "sending", "rejected"])
def test_queue_attention(db_path, state):
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE backup_outbox SET status=?", (state,))
        db.execute("UPDATE backup_records SET created=?", (NOW - 601,))
    assert status.check_queue(db_path, NOW)["status"] == "attention"


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE backup_outbox SET status='SECRET_CORRUPTED_STATE'",
        "DELETE FROM backup_records",
        "UPDATE backup_records SET created=NULL",
    ],
)
def test_inconsistent_queue_fails_unknown_not_healthy(db_path, mutation):
    with sqlite3.connect(db_path) as db:
        db.execute(mutation)
    result = status.check_queue(db_path, NOW)
    assert result == status.unknown("queue")


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "absent.sqlite"
    assert status.check_queue(path, NOW)["status"] == "unknown"
    assert not path.exists()


def test_config_only_extracts_data_directory(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"data_dir": str(tmp_path), "secret": "SENTINEL"}))
    assert status.queue_path(p) == tmp_path / "input-backup.sqlite"
    p.unlink()
    p.symlink_to(tmp_path / "missing")
    with pytest.raises(OSError):
        status.queue_path(p)


def test_collect_missing_probes_are_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(status, "check_health", lambda: status.unknown("https_delivery"))
    monkeypatch.setattr(status, "check_disk", lambda *a: {"name": "disk", "status": "ok"})
    monkeypatch.setattr(status, "unit_properties", lambda: {})
    report, code = status.collect(tmp_path / "missing", tmp_path, NOW)
    assert code == 1 and report["overall"] == "attention"
    assert report["has_unknown"] is True
    assert "SECRET" not in json.dumps(report)


def test_failure_is_not_masked_by_an_unknown_probe():
    report, code = status.summarize(
        [{"name": "disk", "status": "attention"}, status.unknown("queue")], NOW
    )
    assert code == 1 and report["has_attention"] and report["has_unknown"]
    assert report["overall"] == "attention"


def test_inactive_timer_detected_before_backups_go_stale():
    values = {"LoadState": "loaded", "ActiveState": "inactive"}
    assert status.check_unit("cos_timer", values, NOW)["status"] == "attention"
    values.update(ActiveState="active", NextElapseUSecRealtime="Mon 2026-10-05 00:00:00 UTC")
    assert status.check_unit("cos_timer", values, NOW)["status"] == "ok"


def test_collect_checks_separate_storage_and_cos_order(monkeypatch, tmp_path):
    paths = []
    monkeypatch.setattr(status, "check_health", lambda: {"name": "https_delivery", "status": "ok"})

    def disk(path="/"):
        paths.append(path)
        return {"name": "disk", "status": "attention" if path == tmp_path else "ok"}

    monkeypatch.setattr(status, "check_disk", disk)
    monkeypatch.setattr(
        status,
        "check_snapshot",
        lambda *a: {"name": "local_snapshot", "status": "ok", "age_seconds": 30},
    )
    monkeypatch.setattr(status, "unit_properties", lambda: {})
    monkeypatch.setattr(
        status, "check_unit", lambda name, *a: {"name": name, "status": "ok", "age_seconds": 300}
    )
    monkeypatch.setattr(status, "queue_path", lambda *a: tmp_path / "input-backup.sqlite")
    monkeypatch.setattr(status, "check_queue", lambda *a: {"name": "queue", "status": "ok"})
    report, code = status.collect(tmp_path / "config", tmp_path / "backups", NOW)
    assert "/" in paths and tmp_path in paths and tmp_path / "backups" in paths
    assert code == 1
    cos = next(c for c in report["checks"] if c["name"] == "cos_last_run")
    assert cos["reason"] == "cos_run_predates_latest_snapshot"


@pytest.mark.parametrize("created", [None, "invalid", NOW + 120, float("inf"), float("-inf")])
def test_mixed_queue_does_not_hide_invalid_wait_timestamp(db_path, created):
    assert status.check_queue(db_path, NOW)["status"] == "ok"
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("INSERT INTO backup_records VALUES (?,?,?)", ("2", created, "PRIVATE"))
        db.execute("INSERT INTO backup_outbox VALUES (?,?)", ("2", "sending"))
    assert status.check_queue(db_path, NOW) == status.unknown("queue")
