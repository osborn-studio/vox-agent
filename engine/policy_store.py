"""SQLite persistence for admin policy overrides and retention sweep state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from threading import RLock

from runtime.sqlite_policy import configure_sqlite_connection

from .model_policy import ModelPolicy
from .retention_policy import RetentionPolicy


class PolicyStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._db = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        configure_sqlite_connection(self._db)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_policy (
                name TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS retention_sweep (
                id TEXT PRIMARY KEY,
                dry_run INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                result TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_retention_sweep_completed
                ON retention_sweep(completed_at DESC);
            """
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def load_retention_policy(self) -> RetentionPolicy:
        payload = self._load_policy("retention")
        return RetentionPolicy(**payload) if payload else RetentionPolicy()

    def save_retention_policy(self, policy: RetentionPolicy) -> None:
        self._save_policy("retention", asdict(policy))

    def load_model_policy(self) -> ModelPolicy:
        payload = self._load_policy("model")
        return ModelPolicy(**payload) if payload else ModelPolicy()

    def save_model_policy(self, policy: ModelPolicy) -> None:
        self._save_policy("model", asdict(policy))

    def record_retention_sweep(self, result: dict) -> None:
        with self._lock:
            self._db.execute(
                """INSERT INTO retention_sweep
                   (id, dry_run, started_at, completed_at, result)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    result["id"],
                    int(result["dryRun"]),
                    result["startedAt"],
                    result["completedAt"],
                    json.dumps(result, separators=(",", ":")),
                ),
            )
            self._db.commit()

    def latest_retention_sweep(self) -> dict | None:
        with self._lock:
            row = self._db.execute(
                """SELECT result FROM retention_sweep
                   ORDER BY completed_at DESC, rowid DESC LIMIT 1"""
            ).fetchone()
        return json.loads(row["result"]) if row else None

    def _load_policy(self, name: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT payload FROM admin_policy WHERE name = ?",
                (name,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def _save_policy(self, name: str, payload: dict) -> None:
        with self._lock:
            self._db.execute(
                """INSERT INTO admin_policy (name, payload, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(name) DO UPDATE SET
                       payload = excluded.payload,
                       updated_at = excluded.updated_at""",
                (name, json.dumps(payload, separators=(",", ":"))),
            )
            self._db.commit()
