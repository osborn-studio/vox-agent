"""Durable retention policy and safe sweeper behavior."""

import asyncio
import json
from datetime import datetime, timezone

from engine.policy_store import PolicyStore
from engine.retention import RetentionScheduler, RetentionSweeper
from engine.retention_policy import RetentionPolicy
from engine.voice_audit import VoiceAuditLog
from engine.voice_memory_sqlite import SqliteVoiceMemoryRepository

FIXED_NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)
OLD = "2026-01-01T00:00:00+00:00"
RECENT = "2026-07-10T00:00:00+00:00"


def _set_session_created_at(repo, session_id, created_at):
    row = repo._db.execute(
        "SELECT data FROM capture_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    data = json.loads(row[0])
    data["createdAt"] = created_at
    repo._db.execute(
        "UPDATE capture_sessions SET created_at = ?, data = ? WHERE id = ?",
        (created_at, json.dumps(data), session_id),
    )
    repo._db.commit()


def _session_payload(title):
    return {
        "workspaceId": "ws_1",
        "ownerUserId": "user_1",
        "source": "WEB",
        "mode": "MEETING",
        "title": title,
        "languageHints": [],
        "privacyLevel": "PERSONAL",
        "consentState": "GRANTED",
        "deviceIds": [],
        "localOnlyUntilSync": False,
    }


def _segment_payload(text):
    return {
        "speakerId": "speaker_1",
        "startMs": 0,
        "endMs": 100,
        "textRaw": text,
        "textNormalized": text,
        "model": "mock",
        "language": "en",
    }


def _node_payload(session_id, title):
    return {
        "workspaceId": "ws_1",
        "sourceSessionId": session_id,
        "type": "SUMMARY",
        "title": title,
        "body": title,
        "tags": [],
        "confidence": 1.0,
    }


def test_policy_overrides_survive_store_restart(tmp_path):
    store = PolicyStore(tmp_path / "admin_state.db")
    policy = RetentionPolicy(audio_retention_days=14, auto_delete_enabled=True)
    store.save_retention_policy(policy)
    store.close()

    restarted = PolicyStore(tmp_path / "admin_state.db")
    loaded = restarted.load_retention_policy()

    assert loaded.audio_retention_days == 14
    assert loaded.auto_delete_enabled is True


def test_sweeper_dry_run_then_deletes_only_expired_records(tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    repo = SqliteVoiceMemoryRepository(tmp_path / "voice.db")
    old_session = repo.create_session(_session_payload("old"))
    recent_session = repo.create_session(_session_payload("recent"))
    _set_session_created_at(repo, old_session["id"], OLD)
    _set_session_created_at(repo, recent_session["id"], RECENT)
    repo.add_segment(old_session["id"], _segment_payload("old transcript"))
    repo.add_segment(recent_session["id"], _segment_payload("recent transcript"))

    old_audio = audio_dir / "old.wav"
    recent_audio = audio_dir / "recent.wav"
    old_audio.write_bytes(b"old")
    recent_audio.write_bytes(b"recent")
    repo.set_audio_path(old_session["id"], str(old_audio))
    repo.set_audio_path(recent_session["id"], str(recent_audio))

    old_node = repo.add_memory_node(_node_payload(old_session["id"], "old memory"))
    recent_node = repo.add_memory_node(_node_payload(recent_session["id"], "recent memory"))
    repo._db.execute("UPDATE memory_nodes SET created_at = ? WHERE id = ?", (OLD, old_node["id"]))
    repo._db.execute("UPDATE memory_nodes SET created_at = ? WHERE id = ?", (RECENT, recent_node["id"]))
    repo._db.commit()

    policy = RetentionPolicy(
        audio_retention_days=30,
        transcript_retention_days=30,
        memory_retention_days=30,
        auto_delete_enabled=True,
    )
    audit = VoiceAuditLog(tmp_path / "audit.db")
    state = PolicyStore(tmp_path / "admin_state.db")
    sweeper = RetentionSweeper(
        repository=repo,
        policy=policy,
        audit_log=audit,
        state_store=state,
        allowed_audio_dirs=[audio_dir],
        now=lambda: FIXED_NOW,
    )

    preview = sweeper.run(dry_run=True, actor_id="admin")
    assert preview["candidates"] == {"audio": 1, "transcripts": 1, "memory": 1}
    assert preview["deleted"] == {"audio": 0, "transcripts": 0, "memory": 0}
    assert old_audio.exists()
    assert len(repo.list_segments(old_session["id"])) == 1

    result = sweeper.run(dry_run=False, actor_id="admin")
    assert result["deleted"] == {"audio": 1, "transcripts": 1, "memory": 1}
    assert not old_audio.exists()
    assert recent_audio.exists()
    assert repo.list_segments(old_session["id"]) == []
    assert len(repo.list_segments(recent_session["id"])) == 1
    assert repo.get_node(old_node["id"]) is None
    assert repo.get_node(recent_node["id"]) is not None
    assert audit.list_events()[0]["action"] == "admin.retention-sweep.executed"
    assert state.latest_retention_sweep()["id"] == result["id"]


def test_sweeper_refuses_to_unlink_audio_outside_allowed_roots(tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"keep")
    repo = SqliteVoiceMemoryRepository(tmp_path / "voice.db")
    session = repo.create_session(_session_payload("old"))
    _set_session_created_at(repo, session["id"], OLD)
    repo.set_audio_path(session["id"], str(outside))
    sweeper = RetentionSweeper(
        repository=repo,
        policy=RetentionPolicy(audio_retention_days=30, auto_delete_enabled=True),
        audit_log=VoiceAuditLog(),
        state_store=PolicyStore(tmp_path / "admin_state.db"),
        allowed_audio_dirs=[audio_dir],
        now=lambda: FIXED_NOW,
    )

    result = sweeper.run(dry_run=False, actor_id="admin")

    assert outside.exists()
    assert result["deleted"]["audio"] == 0
    assert result["failures"][0]["code"] == "AUDIO_PATH_OUTSIDE_ALLOWED_ROOT"
    assert repo.get_audio_path(session["id"]) == str(outside)


async def test_scheduler_runs_enabled_policy_and_stops_cleanly():
    class FakeSweeper:
        policy = RetentionPolicy(auto_delete_enabled=True)
        audit_log = VoiceAuditLog()

        def __init__(self):
            self.calls = []

        def run(self, *, dry_run, actor_id):
            self.calls.append((dry_run, actor_id))

    sweeper = FakeSweeper()
    scheduler = RetentionScheduler(sweeper)

    scheduler.start()
    for _ in range(20):
        if sweeper.calls:
            break
        await asyncio.sleep(0.01)
    await scheduler.stop()

    assert sweeper.calls == [(False, "system:retention-scheduler")]


async def test_scheduler_stop_tolerates_runtime_cancelling_background_task():
    class FakeSweeper:
        policy = RetentionPolicy(auto_delete_enabled=False)
        audit_log = VoiceAuditLog()

    scheduler = RetentionScheduler(FakeSweeper())
    scheduler.start()
    scheduler._task.cancel()

    await scheduler.stop()

    assert scheduler._task is None
def test_policy_and_audit_databases_use_windows_safe_connection_policy(tmp_path):
    policy = PolicyStore(tmp_path / "admin_state.db")
    audit = VoiceAuditLog(tmp_path / "audit.db")

    for connection in (policy._db, audit._db):
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    policy.close()
    audit.close()
