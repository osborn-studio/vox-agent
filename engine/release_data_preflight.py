"""Bounded, secret-free release preflight for VoxLog-to-Vox Agent upgrades.

The preflight only considers an explicit file allowlist. SQLite sources are
snapshotted with the online backup API, JSON files containing secret-like keys
are rejected, and restores are allowed only into an empty non-live home.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from runtime.data_paths import resolve_vox_agent_data_dir

MAX_PREFLIGHT_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_PREFLIGHT_JSON_BYTES = 32 * 1024 * 1024
MAX_PREFLIGHT_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_SQLITE_TABLES = 128

_JSON_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")
_SECRET_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "apikey",
    "accesskey",
    "privatekey",
    "credential",
    "authorization",
)
_SECRET_FILES = (
    ".env",
    ".env.local",
    "credentials.json",
    "secrets.json",
)

_V2_FILES = (
    "history.db",
    "terms.json",
    "settings.json",
)
_SWIFT_FILES = (
    "history.db",
    "messages.db",
    "terms.json",
    "settings.json",
    "modes.json",
    "agents.json",
    "groups.json",
    "tasks.json",
    "openclaw-bindings.json",
    "snippets.json",
    "hotwords.json",
    "sync-manifest.json",
    "notion-sync-manifest.json",
)
_VOX_AGENT_FILES = (
    "history.db",
    "tasks.db",
    "activity.db",
    "messages.db",
    "admin_audit.db",
    "admin_state.db",
    "org.db",
    "voice_memory.db",
    "work_events.db",
    "access_audit.db",
    "terms.json",
    "settings.json",
    "agents.json",
    "groups.json",
    "tasks.json",
    "openclaw-bindings.json",
    "permission_rules.json",
    "schedules.json",
)


@dataclass(frozen=True)
class _SourceRoot:
    source: str
    path: Path
    display_root: str
    restore_root: Path
    allowed_files: tuple[str, ...]


def build_release_preflight(
    *,
    home: Path,
    canonical_data_dir: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Inspect allowlisted mutable data without writing to any source."""

    roots = _source_roots(home=home, canonical_data_dir=canonical_data_dir)
    artifacts: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    total_bytes = 0

    for root in roots:
        for relative in root.allowed_files:
            path = root.path / relative
            if not path.exists() and not path.is_symlink():
                continue
            display_path = _display_path(root, relative)
            if path.is_symlink():
                excluded.append(_excluded(root.source, display_path, "symbolic link"))
                continue
            if not path.is_file():
                excluded.append(_excluded(root.source, display_path, "not a regular file"))
                continue
            size = path.stat().st_size
            if size > MAX_PREFLIGHT_FILE_BYTES:
                excluded.append(_excluded(root.source, display_path, "file exceeds safety limit"))
                continue
            if path.suffix != ".db" and size > MAX_PREFLIGHT_JSON_BYTES:
                excluded.append(
                    _excluded(root.source, display_path, "JSON file exceeds safety limit")
                )
                continue

            artifact: dict[str, Any] = {
                "source": root.source,
                "relativePath": relative,
                "displayPath": display_path,
                "kind": "sqlite" if path.suffix == ".db" else "json",
                "sizeBytes": size,
                "sha256": _sha256(path),
            }
            if path.suffix == ".db":
                artifact["sqlite"] = _inspect_sqlite(path)
            else:
                json_metadata, secret_like = _inspect_json(path)
                if secret_like:
                    excluded.append(
                        _excluded(root.source, display_path, "secret-like JSON key")
                    )
                    continue
                artifact["json"] = json_metadata
            artifacts.append(artifact)
            total_bytes += size

        for relative in _SECRET_FILES:
            path = root.path / relative
            if path.exists() or path.is_symlink():
                excluded.append(
                    _excluded(root.source, _display_path(root, relative), "secret-bearing file")
                )

    if total_bytes > MAX_PREFLIGHT_TOTAL_BYTES:
        raise ValueError("release preflight exceeds the total backup safety limit")

    artifacts.sort(key=lambda item: (item["source"], item["relativePath"]))
    excluded.sort(key=lambda item: (item["source"], item["displayPath"]))
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at or datetime.now(timezone.utc).isoformat(),
        "dryRun": True,
        "policy": {
            "allowlistedFilesOnly": True,
            "sqliteUsesOnlineBackup": True,
            "keychainExported": False,
            "excludedCategories": [
                "Keychain and credentials files",
                "environment files",
                "model downloads",
                "caches and logs",
                "unrelated home directories",
            ],
        },
        "summary": {
            "includedFiles": len(artifacts),
            "excludedFiles": len(excluded),
            "includedBytes": total_bytes,
        },
        "artifacts": artifacts,
        "excluded": excluded,
    }


def create_release_backup(
    *,
    home: Path,
    backup_root: Path,
    canonical_data_dir: Path | None = None,
    generated_at: str | None = None,
    backup_id: str | None = None,
) -> Path:
    """Create a timestamped, allowlisted backup and return its manifest path."""

    roots = _source_roots(home=home, canonical_data_dir=canonical_data_dir)
    resolved_backup_root = backup_root.expanduser().resolve()
    _validate_backup_root(resolved_backup_root, roots)
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    identifier = backup_id or (
        "vox-agent-preflight-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"-{uuid4().hex[:8]}"
    )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", identifier):
        raise ValueError("backup ID contains unsupported characters")

    preflight = build_release_preflight(
        home=home,
        canonical_data_dir=canonical_data_dir,
        generated_at=timestamp,
    )
    backup_dir = resolved_backup_root / identifier
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    backup_dir.chmod(0o700)
    roots_by_id = {root.source: root for root in roots}

    try:
        backed_artifacts: list[dict[str, Any]] = []
        for artifact in preflight["artifacts"]:
            root = roots_by_id[artifact["source"]]
            source = root.path / artifact["relativePath"]
            backup_relative = (
                Path("data") / artifact["source"] / artifact["relativePath"]
            )
            destination = backup_dir / backup_relative
            _secure_mkdir_chain(destination.parent, boundary=backup_dir)
            if artifact["kind"] == "sqlite":
                _snapshot_database(source, destination)
            else:
                _copy_json_snapshot(
                    source,
                    destination,
                    expected_sha256=artifact["sha256"],
                )
            destination.chmod(0o600)

            backed = dict(artifact)
            backed["backupPath"] = backup_relative.as_posix()
            backed["restoreRoot"] = root.restore_root.as_posix()
            backed["backupSha256"] = _sha256(destination)
            if artifact["kind"] == "sqlite":
                backed["backupSqlite"] = _inspect_sqlite(destination)
                if backed["backupSqlite"] != artifact["sqlite"]:
                    raise RuntimeError(
                        f"SQLite backup count mismatch for {artifact['displayPath']}"
                    )
            backed_artifacts.append(backed)

        manifest = dict(preflight)
        manifest["dryRun"] = False
        manifest["backupId"] = identifier
        manifest["artifacts"] = backed_artifacts
        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)
        return manifest_path
    except Exception:
        shutil.rmtree(backup_dir)
        raise


def restore_release_backup(
    *,
    manifest_path: Path,
    restore_home: Path,
) -> dict[str, Any]:
    """Restore a release backup into an empty temporary home and verify it."""

    manifest_file = manifest_path.expanduser().resolve()
    if not manifest_file.is_file() or manifest_file.name != "manifest.json":
        raise ValueError("release backup manifest was not found")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 1 or manifest.get("dryRun") is not False:
        raise ValueError("unsupported release backup manifest")

    target_home = restore_home.expanduser().resolve()
    if target_home == Path.home().resolve():
        raise ValueError("refusing to restore over the live home")
    if target_home == Path(target_home.anchor):
        raise ValueError("refusing to use a filesystem root as restore home")
    if target_home.exists() and any(target_home.iterdir()):
        raise ValueError("restore home must be empty")
    target_home.mkdir(parents=True, exist_ok=True)

    backup_dir = manifest_file.parent
    restored = 0
    for artifact in manifest.get("artifacts", []):
        source = _safe_backup_file(backup_dir, artifact["backupPath"])
        if _sha256(source) != artifact["backupSha256"]:
            raise RuntimeError(f"backup hash mismatch for {artifact['displayPath']}")
        destination = _safe_restore_file(
            target_home,
            artifact["restoreRoot"],
            artifact["relativePath"],
        )
        if destination.exists() or destination.is_symlink():
            raise RuntimeError(f"restore destination already exists: {artifact['displayPath']}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
        if _sha256(destination) != artifact["backupSha256"]:
            raise RuntimeError(f"restored hash mismatch for {artifact['displayPath']}")
        if artifact["kind"] == "sqlite":
            restored_sqlite = _inspect_sqlite(destination)
            if restored_sqlite != artifact["backupSqlite"]:
                raise RuntimeError(
                    f"restored SQLite count mismatch for {artifact['displayPath']}"
                )
        restored += 1

    return {
        "verified": True,
        "backupId": manifest["backupId"],
        "restoredFiles": restored,
        "restoreHome": str(target_home),
    }


def _source_roots(
    *,
    home: Path,
    canonical_data_dir: Path | None,
) -> tuple[_SourceRoot, ...]:
    resolved_home = home.expanduser().resolve()
    canonical = (
        canonical_data_dir.expanduser().resolve()
        if canonical_data_dir is not None
        else resolve_vox_agent_data_dir(home=resolved_home).resolve()
    )
    return (
        _SourceRoot(
            "voxlog-v2",
            resolved_home / ".voxlog2",
            "~/.voxlog2",
            Path(".voxlog2"),
            _V2_FILES,
        ),
        _SourceRoot(
            "voxlog-v1",
            resolved_home / ".voxlog",
            "~/.voxlog",
            Path(".voxlog"),
            _V2_FILES,
        ),
        _SourceRoot(
            "voxlog-swift",
            resolved_home / "Library" / "Application Support" / "VoxLog",
            "~/Library/Application Support/VoxLog",
            Path("Library") / "Application Support" / "VoxLog",
            _SWIFT_FILES,
        ),
        _SourceRoot(
            "vox-agent",
            canonical,
            "~/.voxagent",
            Path(".voxagent"),
            _VOX_AGENT_FILES,
        ),
    )


def _inspect_sqlite(path: Path) -> dict[str, Any]:
    encoded = quote(str(path.resolve()), safe="/")
    with closing(sqlite3.connect(f"file:{encoded}?mode=ro", uri=True, timeout=5)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        if len(tables) > MAX_SQLITE_TABLES:
            raise ValueError(f"SQLite source has more than {MAX_SQLITE_TABLES} tables")
        counts = {
            table: int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                ).fetchone()[0]
            )
            for table in tables
        }
    return {"userVersion": user_version, "tableCounts": counts}


def _inspect_json(path: Path) -> tuple[dict[str, Any], bool]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON in release data file: {path.name}") from exc
    if _contains_secret_key(payload):
        return {}, True
    metadata: dict[str, Any] = {
        "itemCount": len(payload) if isinstance(payload, (dict, list)) else 1,
    }
    if isinstance(payload, dict):
        version = payload.get("schemaVersion", payload.get("version"))
        if isinstance(version, int) and not isinstance(version, bool):
            metadata["version"] = version
        elif isinstance(version, str) and _JSON_VERSION.fullmatch(version):
            metadata["version"] = version
    return metadata, False


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                return True
            if _contains_secret_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def _snapshot_database(source: Path, destination: Path) -> None:
    encoded = quote(str(source.resolve()), safe="/")
    with closing(
        sqlite3.connect(f"file:{encoded}?mode=ro", uri=True, timeout=5)
    ) as source_db:
        source_db.execute("PRAGMA query_only=ON")
        source_db.execute("PRAGMA busy_timeout=5000")
        with closing(sqlite3.connect(destination, timeout=5)) as destination_db:
            source_db.backup(destination_db)


def _copy_json_snapshot(source: Path, destination: Path, *, expected_sha256: str) -> None:
    if source.is_symlink():
        raise RuntimeError(f"JSON source became a symbolic link: {source.name}")
    content = source.read_bytes()
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != expected_sha256:
        raise RuntimeError(f"JSON source changed after preflight: {source.name}")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"JSON source became invalid after preflight: {source.name}") from exc
    if _contains_secret_key(payload):
        raise RuntimeError(f"JSON source gained a secret-like key: {source.name}")
    destination.write_bytes(content)
    shutil.copystat(source, destination, follow_symlinks=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _secure_mkdir_chain(path: Path, *, boundary: Path) -> None:
    if boundary != path and boundary not in path.parents:
        raise ValueError("backup directory escapes its boundary")
    missing: list[Path] = []
    current = path
    while current != boundary and not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)


def _display_path(root: _SourceRoot, relative: str) -> str:
    return f"{root.display_root}/{relative}"


def _excluded(source: str, display_path: str, reason: str) -> dict[str, str]:
    return {"source": source, "displayPath": display_path, "reason": reason}


def _validate_backup_root(backup_root: Path, roots: tuple[_SourceRoot, ...]) -> None:
    if backup_root == Path(backup_root.anchor):
        raise ValueError("backup root cannot be a filesystem root")
    for root in roots:
        resolved = root.path.resolve()
        if backup_root == resolved or resolved in backup_root.parents:
            raise ValueError("backup root must be outside VoxLog and Vox Agent data directories")


def _safe_backup_file(backup_dir: Path, relative: str) -> Path:
    unresolved = backup_dir / relative
    if unresolved.is_symlink():
        raise ValueError("backup artifact is a symbolic link")
    candidate = unresolved.resolve()
    if backup_dir != candidate and backup_dir not in candidate.parents:
        raise ValueError("backup path escapes the backup directory")
    if not candidate.is_file():
        raise ValueError("backup artifact is missing")
    return candidate


def _safe_restore_file(home: Path, restore_root: str, relative: str) -> Path:
    candidate = (home / restore_root / relative).resolve()
    if home != candidate and home not in candidate.parents:
        raise ValueError("restore path escapes the temporary home")
    return candidate
