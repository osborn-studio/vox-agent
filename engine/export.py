"""Export — generate reports from task execution data.

Supports multiple output formats:
- Markdown (for Obsidian, GitHub, docs)
- JSON (for programmatic consumption)
- CSV (for spreadsheets)
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone, timedelta

from .store import StoreManager
from .analytics import TaskAnalytics

logger = logging.getLogger(__name__)


class ReportExporter:
    """Generate reports from engine data."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store
        self.analytics = TaskAnalytics(store)

    def daily_report(self, date: str | None = None) -> str:
        """Generate a daily activity report in Markdown."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        tasks = [t for t in self.store.list_tasks() if t["created_at"].startswith(date)]
        if not tasks:
            return f"# Daily Report {date}\n\nNo tasks executed.\n"

        completed = [t for t in tasks if t["status"] == "completed"]
        failed = [t for t in tasks if t["status"] == "failed"]

        lines = [
            f"# Daily Report {date}",
            "",
            f"> {len(tasks)} tasks | {len(completed)} completed | {len(failed)} failed",
            "",
        ]

        # By agent
        agents: dict[str, list] = {}
        for t in tasks:
            agents.setdefault(t["agent_id"], []).append(t)

        for agent_id in sorted(agents):
            agent_tasks = agents[agent_id]
            lines.append(f"## {agent_id}")
            lines.append("")
            for t in agent_tasks:
                emoji = {"completed": "✅", "failed": "❌", "cancelled": "🗑️",
                         "running": "🔄"}.get(t["status"], "📋")
                time_str = t["created_at"][11:16] if len(t["created_at"]) > 16 else ""
                lines.append(f"- {emoji} {time_str} **{t['title']}** ({t['status']})")
            lines.append("")

        return "\n".join(lines)

    def weekly_report(self) -> str:
        """Generate a weekly summary report."""
        stats = self.analytics.engine_stats()
        today = datetime.now(timezone.utc).date()
        week_start = (today - timedelta(days=today.weekday())).isoformat()

        lines = [
            f"# Weekly Report ({week_start} ~ {today.isoformat()})",
            "",
            f"> {stats.tasks_this_week} tasks this week",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total tasks | {stats.total_tasks} |",
            f"| Completed | {stats.completed} |",
            f"| Failed | {stats.failed} |",
            f"| Active | {stats.active} |",
            "",
        ]

        if stats.agents:
            lines.append("## Per Agent")
            lines.append("")
            lines.append("| Agent | Tasks | Success Rate | Avg Duration |")
            lines.append("|-------|-------|-------------|-------------|")
            for a in stats.agents:
                rate = f"{a.success_rate:.0%}" if a.total_tasks > 0 else "—"
                dur = f"{a.avg_duration_sec:.1f}s" if a.avg_duration_sec > 0 else "—"
                lines.append(f"| {a.agent_id} | {a.total_tasks} | {rate} | {dur} |")
            lines.append("")

        return "\n".join(lines)

    def tasks_csv(self, agent_id: str | None = None) -> str:
        """Export tasks as CSV."""
        tasks = self.store.list_tasks(agent_id=agent_id)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "agent_id", "title", "status", "priority", "created_at", "completed_at", "error"])
        for t in tasks:
            writer.writerow([
                t["id"], t["agent_id"], t["title"], t["status"],
                t["priority"], t["created_at"], t.get("completed_at", ""), t.get("error", ""),
            ])
        return output.getvalue()

    def tasks_json(self, agent_id: str | None = None) -> str:
        """Export tasks as JSON."""
        tasks = self.store.list_tasks(agent_id=agent_id)
        return json.dumps(tasks, indent=2, ensure_ascii=False, default=str)
