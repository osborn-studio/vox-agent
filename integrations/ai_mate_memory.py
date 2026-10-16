"""Minimal AI Mate Memory sink for VoxLog digests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class AIMateMemoryExportResult:
    base_path: str
    record_path: str
    bytes_written: int


class AIMateMemorySink:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir).expanduser()

    def export_digest(
        self,
        *,
        scope: str,
        markdown: str,
        session_id: str = "",
        digest_date: str = "",
        project_key: str = "",
    ) -> AIMateMemoryExportResult:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "voxlog_digest",
            "scope": scope.strip().lower(),
            "session_id": session_id.strip(),
            "digest_date": digest_date.strip(),
            "project_key": project_key.strip().lower(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "content_markdown": markdown,
        }
        target = self._target_path(
            scope=payload["scope"],
            session_id=payload["session_id"],
            digest_date=payload["digest_date"],
            project_key=payload["project_key"],
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        target.write_text(body, encoding="utf-8")
        return AIMateMemoryExportResult(
            base_path=str(self.base_dir),
            record_path=str(target),
            bytes_written=len(body.encode("utf-8")),
        )

    def _target_path(self, *, scope: str, session_id: str, digest_date: str, project_key: str) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if scope == "session":
            key = session_id or "unknown-session"
            return self.base_dir / "voxlog" / "sessions" / f"{key}-{stamp}.json"
        if scope == "daily":
            key = digest_date or "unknown-day"
            return self.base_dir / "voxlog" / "daily" / f"{key}-{stamp}.json"
        if scope == "project":
            key = project_key or "unknown-project"
            return self.base_dir / "voxlog" / "projects" / f"{key}-{stamp}.json"
        return self.base_dir / "voxlog" / "inbox" / f"{scope or 'digest'}-{stamp}.json"
