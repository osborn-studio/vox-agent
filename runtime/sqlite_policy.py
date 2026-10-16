"""Shared SQLite connection policy for Vox Agent-owned databases."""

from __future__ import annotations

import sqlite3

WINDOWS_SAFE_SQLITE_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
)


def configure_sqlite_connection(connection: sqlite3.Connection) -> None:
    """Apply the bounded cross-platform policy to one stdlib connection."""

    for statement in WINDOWS_SAFE_SQLITE_PRAGMAS:
        connection.execute(statement)
