"""Task Archive — move old completed tasks to cold storage.

Keeps the active tasks.db lean by archiving completed/failed tasks
older than a configurable threshold to a separate archive file.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from runtime.data_paths import resolve_vox_agent_data_dir

from .store import StoreManager

logger = logging.getLogger(__name__)


class TaskArchiver:
    """Archive old tasks to reduce active database size."""

    def __init__(self, store: StoreManager, archive_dir: Path | None = None) -> None:
        self.store = store
        self.archive_dir = archive_dir or (resolve_vox_agent_data_dir() / "archive")

    def archive_old(self, days: int = 30) -> int:
        """Move tasks older than N days to archive. Returns count archived."""
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        self.store._ensure_connected()
        rows = self.store._tasks_db.execute(
            """SELECT * FROM task
               WHERE status IN ('completed', 'failed', 'cancelled')
               AND created_at < ?
               ORDER BY created_at""",
            (cutoff,),
        ).fetchall()

        if not rows:
            return 0

        # Write to archive file (monthly)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        archive_path = self.archive_dir / f"tasks-{month}.jsonl"

        with open(archive_path, "a") as f:
            for row in rows:
                task_data = dict(row)
                # Include messages
                msgs = self.store.list_task_messages(task_data["id"])
                task_data["messages"] = msgs
                f.write(json.dumps(task_data, default=str) + "\n")

        # Delete from active database
        task_ids = [dict(r)["id"] for r in rows]
        for tid in task_ids:
            self.store._tasks_db.execute(
                "DELETE FROM task_message WHERE task_id = ?", (tid,)
            )
            self.store._tasks_db.execute(
                "DELETE FROM task WHERE id = ?", (tid,)
            )
        self.store._tasks_db.commit()

        logger.info("archived %d tasks (older than %d days) to %s", len(task_ids), days, archive_path)
        return len(task_ids)

    def list_archives(self) -> list[dict]:
        """List available archive files."""
        if not self.archive_dir.exists():
            return []
        result = []
        for path in sorted(self.archive_dir.glob("tasks-*.jsonl"), reverse=True):
            line_count = sum(1 for _ in open(path))
            result.append({
                "file": path.name,
                "month": path.stem.replace("tasks-", ""),
                "tasks": line_count,
                "size_bytes": path.stat().st_size,
            })
        return result

    def search_archive(self, query: str, month: str | None = None) -> list[dict]:
        """Search archived tasks by text."""
        results = []
        if not self.archive_dir.exists():
            return results

        files = [self.archive_dir / f"tasks-{month}.jsonl"] if month else sorted(self.archive_dir.glob("tasks-*.jsonl"))

        for path in files:
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    task = json.loads(line)
                    if query.lower() in json.dumps(task, ensure_ascii=False).lower():
                        results.append({
                            "id": task.get("id", ""),
                            "title": task.get("title", ""),
                            "status": task.get("status", ""),
                            "agent_id": task.get("agent_id", ""),
                            "created_at": task.get("created_at", ""),
                            "archive_file": path.name,
                        })
                except json.JSONDecodeError:
                    continue

        return results[:50]  # limit results

    def restore(self, task_id: str) -> bool:
        """Restore an archived task back to the active database."""
        for path in self.archive_dir.glob("tasks-*.jsonl"):
            lines = path.read_text().splitlines()
            remaining = []
            restored = False

            for line in lines:
                if not line.strip():
                    continue
                try:
                    task = json.loads(line)
                    if task.get("id") == task_id:
                        # Restore to active DB
                        self.store._ensure_connected()
                        self.store._tasks_db.execute(
                            """INSERT OR IGNORE INTO task
                               (id, agent_id, title, status, priority, prompt, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (task["id"], task.get("agent_id", ""), task.get("title", ""),
                             task.get("status", "completed"), task.get("priority", 0),
                             task.get("prompt", ""), task.get("created_at", "")),
                        )
                        self.store._tasks_db.commit()
                        restored = True
                    else:
                        remaining.append(line)
                except json.JSONDecodeError:
                    remaining.append(line)

            if restored:
                path.write_text("\n".join(remaining) + "\n" if remaining else "")
                logger.info("restored task %s from archive", task_id)
                return True

        return False
