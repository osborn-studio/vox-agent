"""Legacy VoxLog to Vox Agent migration compatibility entrypoint.

This module keeps the historical ``V2Migrator`` API for callers, but its target
is now the canonical Vox Agent ``history.db`` and ``terms.json``. Sources are
opened read-only and remain recoverable after a successful import.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from engine.legacy_migration import read_legacy_database
from memory.sqlite_store import SQLiteStore
from runtime.models.config import resolve_vox_agent_data_dir

logger = logging.getLogger(__name__)


@dataclass
class MigrationResult:
    records_migrated: int = 0
    records_skipped: int = 0
    invalid_records: int = 0
    terms_migrated: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors


class V2Migrator:
    """Copy legacy VoxLog V2 data into the canonical Vox Agent data directory."""

    def __init__(
        self,
        v2_data_dir: Path | None = None,
        vox_data_dir: Path | None = None,
    ) -> None:
        self.v2_dir = v2_data_dir or Path.home() / ".voxlog2"
        self.vox_dir = vox_data_dir or resolve_vox_agent_data_dir()

    @property
    def marker_path(self) -> Path:
        return self.vox_dir / ".legacy-voxlog-migrated"

    def needs_migration(self) -> bool:
        return (self.v2_dir / "history.db").is_file() and not self.marker_path.exists()

    def migrate(self) -> MigrationResult:
        result = MigrationResult()
        if not self.needs_migration():
            logger.info("no legacy VoxLog migration needed")
            return result

        try:
            history = read_legacy_database(self.v2_dir / "history.db")
            imported = asyncio.run(self._import_history(history.records))
            result.records_migrated = imported["imported"]
            result.records_skipped = imported["skipped"]
            result.invalid_records = history.invalid
        except Exception as error:
            result.errors.append(f"history: {error}")
            logger.exception("legacy history migration failed")

        try:
            result.terms_migrated = self._migrate_terms()
        except Exception as error:
            result.errors.append(f"terms: {error}")
            logger.exception("legacy terms migration failed")

        if result.success:
            self.vox_dir.mkdir(parents=True, exist_ok=True)
            self.marker_path.write_text(
                "\n".join(
                    [
                        f"records_migrated={result.records_migrated}",
                        f"records_skipped={result.records_skipped}",
                        f"invalid_records={result.invalid_records}",
                        f"terms_migrated={result.terms_migrated}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
        return result

    async def _import_history(self, records: list[dict]) -> dict[str, int]:
        store = SQLiteStore(self.vox_dir / "history.db")
        await store.init()
        imported = 0
        skipped = 0
        try:
            for offset in range(0, len(records), 200):
                result = await store.import_portable_history(records[offset : offset + 200])
                imported += result["imported"]
                skipped += result["skipped"]
        finally:
            await store.close()
        return {"imported": imported, "skipped": skipped}

    def _migrate_terms(self) -> int:
        source = self.v2_dir / "terms.json"
        if not source.is_file():
            return 0
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("legacy terms file must contain an object")
        corrections = payload.get("corrections", {})
        preserve = payload.get("preserve", [])
        if not isinstance(corrections, dict) or not isinstance(preserve, list):
            raise ValueError("legacy terms file has invalid corrections or preserve fields")

        target = self.vox_dir / "terms.json"
        current = (
            json.loads(target.read_text(encoding="utf-8"))
            if target.is_file()
            else {
                "version": 1,
                "corrections": {},
                "preserve": [],
                "format_rules": {"cn_en_space": True},
            }
        )
        current_corrections = current.setdefault("corrections", {})
        current_preserve = current.setdefault("preserve", [])
        if not isinstance(current_corrections, dict) or not isinstance(current_preserve, list):
            raise ValueError("Vox Agent terms file has invalid fields")

        migrated = 0
        for wrong, right in corrections.items():
            if (
                not isinstance(wrong, str)
                or not wrong.strip()
                or len(wrong) > 200
                or not isinstance(right, str)
                or not right.strip()
                or len(right) > 500
            ):
                raise ValueError("legacy correction exceeds the Vox Agent terms contract")
            if wrong not in current_corrections:
                current_corrections[wrong] = right
                migrated += 1
        known = set(current_preserve)
        for term in preserve:
            if not isinstance(term, str) or not term.strip() or len(term) > 500:
                raise ValueError("legacy preserved term exceeds the Vox Agent terms contract")
            if term not in known:
                current_preserve.append(term)
                known.add(term)
                migrated += 1

        self.vox_dir.mkdir(parents=True, exist_ok=True)
        content = json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.vox_dir,
            prefix="terms.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
        return migrated
