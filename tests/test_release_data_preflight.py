from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

import pytest

from engine import release_data_preflight
from engine.release_data_preflight import (
    build_release_preflight,
    create_release_backup,
    restore_release_backup,
)


def _create_database(path: Path, table: str, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, value TEXT)')
        connection.executemany(
            f'INSERT INTO "{table}" (value) VALUES (?)',
            [(f"row-{index}",) for index in range(rows)],
        )
        connection.execute("PRAGMA user_version=3")


def _seed_release_data(home: Path) -> Path:
    legacy_v2 = home / ".voxlog2"
    _create_database(legacy_v2 / "history.db", "voice_log", 2)
    (legacy_v2 / "terms.json").write_text(
        json.dumps({"version": 1, "preserve": ["Vox Agent"]}),
        encoding="utf-8",
    )
    (legacy_v2 / ".env").write_text("OPENAI_API_KEY=do-not-copy\n", encoding="utf-8")

    swift = home / "Library" / "Application Support" / "VoxLog"
    _create_database(swift / "history.db", "recognition_history", 3)
    (swift / "modes.json").write_text(
        json.dumps({"version": 2, "items": [{"name": "writing"}]}),
        encoding="utf-8",
    )
    (swift / "credentials.json").write_text(
        json.dumps({"apiKey": "do-not-copy"}),
        encoding="utf-8",
    )
    models = swift / "models"
    models.mkdir()
    (models / "model.onnx").write_bytes(b"model-data")

    canonical = home / ".voxagent"
    _create_database(canonical / "tasks.db", "tasks", 1)
    (canonical / "settings.json").write_text(
        json.dumps({"version": 4, "theme": "system"}),
        encoding="utf-8",
    )
    return canonical


def test_dry_run_manifest_contains_only_safe_metadata(tmp_path: Path):
    home = tmp_path / "owner"
    canonical = _seed_release_data(home)

    manifest = build_release_preflight(
        home=home,
        canonical_data_dir=canonical,
        generated_at="2026-07-26T12:00:00+00:00",
    )

    serialized = json.dumps(manifest, sort_keys=True)
    assert "do-not-copy" not in serialized
    assert str(home) not in serialized
    assert manifest["dryRun"] is True
    assert manifest["summary"] == {
        "includedFiles": 6,
        "excludedFiles": 2,
        "includedBytes": manifest["summary"]["includedBytes"],
    }

    artifacts = {
        artifact["displayPath"]: artifact for artifact in manifest["artifacts"]
    }
    assert artifacts["~/.voxlog2/history.db"]["sqlite"] == {
        "userVersion": 3,
        "tableCounts": {"voice_log": 2},
    }
    assert artifacts[
        "~/Library/Application Support/VoxLog/history.db"
    ]["sqlite"]["tableCounts"] == {"recognition_history": 3}
    assert artifacts["~/.voxagent/settings.json"]["json"] == {
        "itemCount": 2,
        "version": 4,
    }
    assert all(len(artifact["sha256"]) == 64 for artifact in manifest["artifacts"])

    excluded = {
        artifact["displayPath"]: artifact["reason"]
        for artifact in manifest["excluded"]
    }
    assert excluded["~/.voxlog2/.env"] == "secret-bearing file"
    assert (
        excluded["~/Library/Application Support/VoxLog/credentials.json"]
        == "secret-bearing file"
    )
    assert "model.onnx" not in serialized


def test_secret_like_allowlisted_json_is_excluded(tmp_path: Path):
    home = tmp_path / "owner"
    canonical = home / ".voxagent"
    canonical.mkdir(parents=True)
    (canonical / "settings.json").write_text(
        json.dumps({"nested": {"accessToken": "do-not-copy"}}),
        encoding="utf-8",
    )

    manifest = build_release_preflight(home=home, canonical_data_dir=canonical)

    assert manifest["artifacts"] == []
    assert manifest["excluded"] == [
        {
            "source": "vox-agent",
            "displayPath": "~/.voxagent/settings.json",
            "reason": "secret-like JSON key",
        }
    ]
    assert "do-not-copy" not in json.dumps(manifest)


def test_oversized_json_is_excluded_before_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "owner"
    canonical = home / ".voxagent"
    canonical.mkdir(parents=True)
    (canonical / "settings.json").write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(release_data_preflight, "MAX_PREFLIGHT_JSON_BYTES", 4)

    manifest = build_release_preflight(home=home, canonical_data_dir=canonical)

    assert manifest["artifacts"] == []
    assert manifest["excluded"][0]["reason"] == "JSON file exceeds safety limit"


def test_backup_and_restore_round_trip_into_temporary_home(tmp_path: Path):
    home = tmp_path / "owner"
    canonical = _seed_release_data(home)
    backup_root = tmp_path / "backups"

    manifest_path = create_release_backup(
        home=home,
        canonical_data_dir=canonical,
        backup_root=backup_root,
        generated_at="2026-07-26T12:00:00+00:00",
        backup_id="test-backup",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["dryRun"] is False
    assert manifest["backupId"] == "test-backup"
    assert manifest_path == backup_root / "test-backup" / "manifest.json"
    assert stat.S_IMODE(manifest_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert all(
        stat.S_IMODE((manifest_path.parent / item["backupPath"]).stat().st_mode)
        == 0o600
        for item in manifest["artifacts"]
    )
    assert not (manifest_path.parent / "voxlog-v2" / ".env").exists()
    assert not (manifest_path.parent / "voxlog-swift" / "credentials.json").exists()
    assert not (manifest_path.parent / "voxlog-swift" / "models").exists()

    restore_home = tmp_path / "restore-home"
    result = restore_release_backup(
        manifest_path=manifest_path,
        restore_home=restore_home,
    )

    assert result["verified"] is True
    assert result["restoredFiles"] == 6
    with sqlite3.connect(restore_home / ".voxlog2" / "history.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM voice_log").fetchone()[0] == 2
    with sqlite3.connect(
        restore_home / "Library" / "Application Support" / "VoxLog" / "history.db"
    ) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM recognition_history"
            ).fetchone()[0]
            == 3
        )
    with sqlite3.connect(restore_home / ".voxagent" / "tasks.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_restore_refuses_live_or_nonempty_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "owner"
    canonical = _seed_release_data(home)
    manifest_path = create_release_backup(
        home=home,
        canonical_data_dir=canonical,
        backup_root=tmp_path / "backups",
        backup_id="test-backup",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    with pytest.raises(ValueError, match="live home"):
        restore_release_backup(manifest_path=manifest_path, restore_home=home)

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("owner data", encoding="utf-8")
    with pytest.raises(ValueError, match="must be empty"):
        restore_release_backup(manifest_path=manifest_path, restore_home=occupied)


def test_symlinked_allowlisted_source_is_not_followed(tmp_path: Path):
    home = tmp_path / "owner"
    canonical = home / ".voxagent"
    canonical.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"version": 1}), encoding="utf-8")
    (canonical / "settings.json").symlink_to(outside)

    manifest = build_release_preflight(home=home, canonical_data_dir=canonical)

    assert manifest["artifacts"] == []
    assert manifest["excluded"][0]["reason"] == "symbolic link"
