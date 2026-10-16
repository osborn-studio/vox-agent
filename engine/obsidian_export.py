"""Obsidian Export — export task execution history to Markdown for knowledge base.

Generates daily task logs alongside the existing voice logs:
  ~/obsidian-vault/06-osborn/agent-logs/YYYY-MM-DD-agent-log.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .store import StoreManager

logger = logging.getLogger(__name__)

DEFAULT_VAULT = Path.home() / "obsidian-vault"
AGENT_LOGS_DIR = "06-osborn/agent-logs"


class ObsidianTaskExporter:
    """Export task execution history to Obsidian as daily Markdown files."""

    def __init__(
        self,
        store: StoreManager,
        vault_path: Path | None = None,
    ) -> None:
        self.store = store
        self.vault = vault_path or DEFAULT_VAULT
        self.logs_dir = self.vault / AGENT_LOGS_DIR

    def export_recent(self, days: int = 7) -> int:
        """Export the last N days of task activity to Obsidian."""
        if not self.vault.exists():
            logger.warning("obsidian vault not found at %s", self.vault)
            return 0

        self.logs_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).date()
        files_written = 0

        for offset in range(days):
            date = today - timedelta(days=offset)
            date_str = date.isoformat()

            tasks = self._tasks_for_date(date_str)
            if not tasks:
                continue

            markdown = self._build_markdown(date_str, tasks)
            path = self.logs_dir / f"{date_str}-agent-log.md"
            path.write_text(markdown)
            files_written += 1
            logger.info("exported %d tasks to %s", len(tasks), path)

        self._update_index()
        return files_written

    def _tasks_for_date(self, date_str: str) -> list[dict]:
        """Get all tasks created on a given date."""
        all_tasks = self.store.list_tasks()
        return [
            t for t in all_tasks
            if t["created_at"].startswith(date_str)
        ]

    def _build_markdown(self, date_str: str, tasks: list[dict]) -> str:
        """Build a Markdown file for a day's task activity."""
        completed = [t for t in tasks if t["status"] == "completed"]
        failed = [t for t in tasks if t["status"] == "failed"]
        cancelled = [t for t in tasks if t["status"] == "cancelled"]

        lines = [
            "---",
            f"date: {date_str}",
            f"tasks: {len(tasks)}",
            f"completed: {len(completed)}",
            f"failed: {len(failed)}",
            "tags: [vox-agents, agent-log]",
            "---",
            "",
            f"# Agent Log {date_str}",
            "",
            f"> {len(tasks)} tasks | {len(completed)} completed | {len(failed)} failed | {len(cancelled)} cancelled",
            "",
        ]

        # Group by agent
        by_agent: dict[str, list[dict]] = {}
        for t in tasks:
            agent = t.get("agent_id", "unknown")
            by_agent.setdefault(agent, []).append(t)

        for agent_id in sorted(by_agent):
            agent_tasks = by_agent[agent_id]
            lines.append(f"## {agent_id}")
            lines.append("")

            for t in sorted(agent_tasks, key=lambda x: x["created_at"]):
                status_emoji = {
                    "completed": "✅",
                    "failed": "❌",
                    "cancelled": "🗑️",
                    "running": "🔄",
                }.get(t["status"], "📋")

                time_str = t["created_at"][11:16] if len(t["created_at"]) > 16 else ""
                lines.append(f"### {time_str} {status_emoji} {t['title']}")
                lines.append("")

                if t.get("description"):
                    lines.append(f"**Prompt:** {t['description'][:200]}")
                    lines.append("")

                if t.get("error"):
                    lines.append(f"**Error:** {t['error'][:200]}")
                    lines.append("")

                # Include task messages
                msgs = self.store.list_task_messages(t["id"])
                if msgs:
                    lines.append("**Execution:**")
                    lines.append("")
                    for msg in msgs[:20]:  # limit to 20 messages
                        msg_type = msg["type"]
                        if msg_type == "text":
                            lines.append(f"- 💬 {msg['content'][:150]}")
                        elif msg_type == "tool_use":
                            lines.append(f"- 🔧 `{msg['tool']}` called")
                        elif msg_type == "tool_result":
                            output_preview = (msg.get("output") or "")[:100]
                            lines.append(f"- ✓ `{msg['tool']}` → {output_preview}")
                        elif msg_type == "error":
                            lines.append(f"- ❌ {msg['content'][:150]}")
                    lines.append("")

                # Duration
                if t.get("started_at") and t.get("completed_at"):
                    try:
                        start = datetime.fromisoformat(t["started_at"])
                        end = datetime.fromisoformat(t["completed_at"])
                        dur = (end - start).total_seconds()
                        lines.append(f"*Duration: {dur:.1f}s*")
                        lines.append("")
                    except (ValueError, TypeError):
                        pass

        return "\n".join(lines) + "\n"

    def _update_index(self) -> None:
        """Update the agent logs index file."""
        if not self.logs_dir.exists():
            return

        log_files = sorted(
            [f.name for f in self.logs_dir.glob("*-agent-log.md")],
            reverse=True,
        )

        lines = [
            "---",
            "tags: [vox-agents, index]",
            "---",
            "",
            "# Vox Agents Task Log Index",
            "",
            "| Date | File |",
            "|------|------|",
        ]
        for fname in log_files:
            date = fname[:10]
            lines.append(f"| {date} | [[{fname}]] |")

        index_path = self.logs_dir / "VoxAgents-Index.md"
        index_path.write_text("\n".join(lines) + "\n")
