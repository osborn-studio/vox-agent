"""Copy-only readers for importing legacy VoxLog history into Vox Agent.

The source databases are always opened read-only. Records are normalized into the
portable Vox Agent history contract and then imported by the canonical SQLiteStore,
which provides stable-ID idempotency and local-wins conflict handling.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

MAX_LEGACY_DATABASE_BYTES = 2 * 1024 * 1024 * 1024
MAX_LEGACY_RECORDS = 50_000
KNOWN_TABLES = ("voice_events", "voice_log", "recognition_history")


@dataclass(frozen=True)
class LegacySource:
    path: Path
    kind: str
    records_found: int


@dataclass
class LegacyHistory:
    source: LegacySource
    records: list[dict] = field(default_factory=list)
    invalid: int = 0


def discover_legacy_databases(
    *,
    home: Path,
    canonical_db: Path,
) -> list[Path]:
    """Return known legacy database locations without modifying any source."""
    candidates = [
        home / ".voxlog2" / "history.db",
        home / ".voxlog" / "history.db",
        home / "Library" / "Application Support" / "VoxLog" / "history.db",
        home / "Library" / "Application Support" / "VoxLog" / "messages.db",
    ]
    canonical = canonical_db.resolve(strict=False)
    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved == canonical or resolved in seen or not candidate.is_file():
            continue
        seen.add(resolved)
        result.append(candidate)
    return result


def inspect_legacy_database(path: Path) -> LegacySource:
    _validate_source(path)
    with _read_only_connection(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        kind = next((table for table in KNOWN_TABLES if table in tables), None)
        if kind is None:
            raise ValueError("legacy database has no supported history table")
        columns = {
            str(row[1]) for row in connection.execute(f'PRAGMA table_info("{kind}")')
        }
        if "created_at" not in columns:
            raise ValueError("legacy history table has no created_at column")
        count = int(connection.execute(f'SELECT COUNT(*) FROM "{kind}"').fetchone()[0])
    if count > MAX_LEGACY_RECORDS:
        raise ValueError(f"legacy database exceeds the {MAX_LEGACY_RECORDS} record limit")
    return LegacySource(path=path, kind=kind, records_found=count)


def read_legacy_database(path: Path) -> LegacyHistory:
    source = inspect_legacy_database(path)
    with _read_only_connection(path) as connection:
        rows = connection.execute(
            f'SELECT * FROM "{source.kind}" ORDER BY created_at ASC LIMIT ?',
            (MAX_LEGACY_RECORDS,),
        ).fetchall()
    history = LegacyHistory(source=source)
    for row in rows:
        try:
            record = _portable_record(dict(row), source.kind)
        except (TypeError, ValueError):
            history.invalid += 1
            continue
        if record["recording_mode"] == "ephemeral":
            history.invalid += 1
            continue
        history.records.append(record)
    return history


def _validate_source(path: Path) -> None:
    if not path.is_file():
        raise ValueError("legacy database was not found")
    size = path.stat().st_size
    if size > MAX_LEGACY_DATABASE_BYTES:
        raise ValueError(
            f"legacy database exceeds the {MAX_LEGACY_DATABASE_BYTES} byte safety limit"
        )


def _read_only_connection(path: Path) -> sqlite3.Connection:
    encoded = quote(str(path.resolve()), safe="/")
    connection = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _portable_record(row: dict, kind: str) -> dict:
    created_at = _timestamp(row.get("created_at"))
    raw_text = _text(row.get("raw_text"), 100_000)
    final_text = _first_text(
        row.get("polished_text"),
        row.get("final_text"),
        row.get("processed_text"),
        row.get("display_text"),
        raw_text,
        maximum=100_000,
    )
    display_text = _first_text(row.get("display_text"), final_text, maximum=100_000)
    event_id = _event_id(row.get("id"), kind, created_at, raw_text, final_text)
    recording_mode = _choice(row.get("recording_mode"), {"normal", "private", "ephemeral"}, "normal")
    archive_status = _choice(
        row.get("archive_status"),
        {"skipped", "queued", "raw_only", "polished", "failed"},
        "polished" if final_text else "raw_only",
    )
    output_mode = _choice(
        row.get("output_mode"),
        {"paste", "direct_typing", "preview_only", "none"},
        "paste",
    )
    target_risk_level = _choice(
        row.get("target_risk_level"), {"low", "medium", "high"}, "low"
    )
    confidence = row.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.0
    confidence = min(max(float(confidence), 0.0), 1.0)
    latency = row.get("latency_total_fast_ms", row.get("latency_ms", 0))
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        latency = 0
    latency = min(max(int(latency), 0), 3_600_000)

    return {
        "id": event_id,
        "created_at": created_at,
        "utterance_id": _text(row.get("utterance_id"), 200),
        "output_id": _text(row.get("output_id"), 200),
        "session_id": _text(row.get("session_id"), 200),
        "raw_text": raw_text,
        "display_text": display_text,
        "polished_text": final_text,
        "stt_provider": _first_text(
            row.get("stt_provider"), row.get("asr_provider"), maximum=200
        ),
        "stt_model": _text(row.get("stt_model"), 200),
        "llm_provider": _text(row.get("llm_provider"), 200),
        "latency_ms": latency,
        "target_app": _text(row.get("target_app"), 500),
        "target_risk_level": target_risk_level,
        "role": _text(row.get("role"), 100) or "me",
        "recording_mode": recording_mode,
        "output_mode": output_mode,
        "confidence": confidence,
        "archive_status": archive_status,
        "agent": _text(row.get("agent"), 200),
    }


def _event_id(value: object, kind: str, created_at: str, raw_text: str, final_text: str) -> str:
    if isinstance(value, str) and value.strip() and len(value) <= 200:
        return value.strip()
    digest = hashlib.sha256(
        f"{kind}\0{created_at}\0{raw_text}\0{final_text}".encode("utf-8")
    ).hexdigest()[:32]
    return f"legacy-{digest}"


def _timestamp(value: object) -> str:
    text = _text(value, 80, required=True)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("legacy timestamp must include a timezone")
    return parsed.isoformat()


def _text(value: object, maximum: int, *, required: bool = False) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    if required and not text.strip():
        raise ValueError("required legacy text is empty")
    if len(text) > maximum:
        raise ValueError("legacy text exceeds the portable contract")
    return text


def _first_text(*values: object, maximum: int) -> str:
    for value in values:
        text = _text(value, maximum)
        if text:
            return text
    return ""


def _choice(value: object, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default
