"""Status Badge — generate status badges for external display.

Creates SVG/text badges showing engine health and task stats
for embedding in README, dashboards, or notifications.
"""

from __future__ import annotations


from .analytics import TaskAnalytics
from .health import HealthChecker


class StatusBadge:
    """Generate status badges."""

    def __init__(self, analytics: TaskAnalytics, health: HealthChecker) -> None:
        self.analytics = analytics
        self.health = health

    def engine_badge(self) -> dict:
        """Engine health badge data."""
        report = self.health.check()
        return {
            "label": "engine",
            "value": report.status,
            "color": {"healthy": "brightgreen", "degraded": "yellow", "unhealthy": "red"}.get(report.status, "gray"),
        }

    def tasks_badge(self) -> dict:
        """Active tasks badge data."""
        stats = self.analytics.engine_stats()
        return {
            "label": "tasks",
            "value": f"{stats.active} active / {stats.total_tasks} total",
            "color": "blue" if stats.active > 0 else "gray",
        }

    def success_badge(self) -> dict:
        """Success rate badge data."""
        stats = self.analytics.engine_stats()
        finished = stats.completed + stats.failed
        rate = stats.completed / finished if finished > 0 else 0
        color = "brightgreen" if rate > 0.9 else "yellow" if rate > 0.7 else "red"
        return {
            "label": "success",
            "value": f"{rate:.0%}",
            "color": color,
        }

    def all_badges(self) -> list[dict]:
        return [self.engine_badge(), self.tasks_badge(), self.success_badge()]

    def text_summary(self) -> str:
        """Plain text status summary."""
        engine = self.engine_badge()
        tasks = self.tasks_badge()
        success = self.success_badge()
        return f"Engine: {engine['value']} | Tasks: {tasks['value']} | Success: {success['value']}"

    def svg(self, badge: dict) -> str:
        """Generate a simple SVG badge."""
        colors = {
            "brightgreen": "#4c1", "green": "#97CA00", "yellow": "#dfb317",
            "orange": "#fe7d37", "red": "#e05d44", "blue": "#007ec6",
            "gray": "#555", "lightgray": "#9f9f9f",
        }
        bg = colors.get(badge["color"], "#555")
        label = badge["label"]
        value = badge["value"]
        width = max(len(label) + len(value), 10) * 7 + 20
        label_width = len(label) * 7 + 10
        value_width = width - label_width

        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20">
  <rect width="{label_width}" height="20" fill="#555"/>
  <rect x="{label_width}" width="{value_width}" height="20" fill="{bg}"/>
  <text x="{label_width // 2}" y="14" fill="#fff" text-anchor="middle" font-size="11" font-family="sans-serif">{label}</text>
  <text x="{label_width + value_width // 2}" y="14" fill="#fff" text-anchor="middle" font-size="11" font-family="sans-serif">{value}</text>
</svg>"""
