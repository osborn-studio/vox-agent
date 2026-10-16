"""Enhanced Obsidian sync with agent/task/session awareness."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_OBSIDIAN_VAULT = Path.home() / "obsidian-vault"


def voice_log_dir(vault_path: Path) -> Path:
    """Return the product-owned archive directory inside a selected vault."""

    return vault_path.expanduser() / "Vox Agent" / "Voice Logs"


@dataclass(frozen=True)
class ObsidianExportResult:
    """Metadata returned after writing a digest note."""

    vault_path: str
    note_path: str
    bytes_written: int


class ObsidianSink:
    """Write scoped digest markdown into a user-selected Obsidian vault."""

    _SCOPE_DIRECTORIES = {
        "session": "Sessions",
        "daily": "Daily",
        "project": "Projects",
    }

    def __init__(self, vault_path: Path) -> None:
        self._vault_path = vault_path.expanduser()

    def export_digest(
        self,
        *,
        scope: str,
        content: str,
        session_id: str = "",
        digest_date: str = "",
        project_key: str = "",
    ) -> ObsidianExportResult:
        normalized_scope = scope.strip().lower()
        if normalized_scope not in self._SCOPE_DIRECTORIES:
            raise ValueError(f"unsupported digest scope: {scope}")

        identifiers = {
            "session": session_id,
            "daily": digest_date,
            "project": project_key,
        }
        identifier = _safe_note_component(identifiers[normalized_scope])
        if not identifier:
            raise ValueError(f"{normalized_scope} digest identifier is required")

        note_dir = self._vault_path / "Vox Agent" / self._SCOPE_DIRECTORIES[normalized_scope]
        note_dir.mkdir(parents=True, exist_ok=True)
        note_path = note_dir / f"{identifier}.md"
        body = content if content.endswith("\n") else f"{content}\n"
        note_path.write_text(body, encoding="utf-8")
        return ObsidianExportResult(
            vault_path=str(self._vault_path),
            note_path=str(note_path),
            bytes_written=len(body.encode("utf-8")),
        )


def _safe_note_component(value: str) -> str:
    """Return a filename component that cannot escape the selected vault."""

    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return normalized[:120]


def sync_day(db_path: Path, date_str: str, vault_path: Path = DEFAULT_OBSIDIAN_VAULT) -> bool:
    """Sync one day's voice logs to Obsidian with enhanced metadata."""
    import sqlite3
    db = sqlite3.connect(str(db_path))

    rows = db.execute(
        """SELECT created_at, raw_text, display_text, polished_text,
                  stt_provider, target_app, role, agent, recording_mode
           FROM voice_events
           WHERE substr(created_at, 1, 10) = ? AND recording_mode = 'normal'
           ORDER BY created_at ASC""",
        (date_str,),
    ).fetchall()

    if not rows:
        db.close()
        return False

    output_dir = voice_log_dir(vault_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{date_str}-voice-log.md"

    # Check if update needed
    if out_path.exists():
        existing_count = out_path.read_text().count("## ")
        if existing_count >= len(rows):
            db.close()
            return False

    # Collect agent stats
    agent_counts: dict[str, int] = {}
    for r in rows:
        agent = r[7] or "general"
        agent_counts[agent] = agent_counts.get(agent, 0) + 1

    # Build markdown
    lines = [
        "---",
        f"date: {date_str}",
        f"recordings: {len(rows)}",
        f"agents: {json.dumps(agent_counts)}",
        "tags: [vox-agent, voice-log]",
        "---",
        "",
        f"# Vox Agent Voice Log: {date_str}",
        "",
        f"> {len(rows)} recordings | Agents: {', '.join(f'{k}({v})' for k,v in sorted(agent_counts.items()))}",
        "",
    ]

    current_agent = ""
    for ts, raw, display, polished, stt, app, role, agent, mode in rows:
        time_part = ts[11:19] if len(ts) > 19 else ts
        text = polished or display or raw
        role_icon = "🎤" if role == "me" else "📋"
        agent_tag = f" `{agent}`" if agent and agent != current_agent else ""
        if agent and agent != current_agent:
            current_agent = agent
            lines.append(f"### {agent}")
            lines.append("")

        app_tag = f" [{app}]" if app else ""
        lines.append(f"## {time_part}{app_tag} {role_icon}{agent_tag}")
        lines.append("")
        lines.append(text)
        lines.append("")

    # Tasks for the day
    task_rows = db.execute(
        """SELECT title, status, assigned_context_id, created_at
           FROM tasks WHERE substr(created_at, 1, 10) = ?""",
        (date_str,),
    ).fetchall() if _table_exists(db, "tasks") else []

    if task_rows:
        lines.append("---")
        lines.append("## Tasks")
        lines.append("")
        for title, status, assigned, created in task_rows:
            lines.append(f"- [{status}] {title} (→ {assigned})")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    db.close()
    return True


def sync_recent(
    db_path: Path,
    vault_path: Path = DEFAULT_OBSIDIAN_VAULT,
    days: int = 7,
) -> list[str]:
    """Sync exactly ``days`` UTC calendar days into the selected vault."""

    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("days must be an integer from 1 through 365")
    now = datetime.now(timezone.utc)
    exported = []
    for i in range(days):
        date_str = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        if sync_day(db_path, date_str, vault_path):
            exported.append(date_str)
    return exported


def _table_exists(db, name: str) -> bool:
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cursor.fetchone() is not None
