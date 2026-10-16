"""Tests for engine.export — report generation."""

import pytest
from engine.export import ReportExporter
from engine.store import StoreManager
from engine.models import Task, TaskStatus


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    exporter = ReportExporter(store)

    # Seed data
    for i, (agent, status) in enumerate([
        ("claude", TaskStatus.COMPLETED),
        ("claude", TaskStatus.FAILED),
        ("codex", TaskStatus.COMPLETED),
        ("claude", TaskStatus.COMPLETED),
    ]):
        t = Task(agent_id=agent, title=f"task-{i}", status=status)
        store.insert_task(t)
        store.update_task_status(t.id, status)

    yield exporter, store
    store.close()


class TestDailyReport:
    def test_markdown(self, components):
        exporter, _ = components
        report = exporter.daily_report()
        assert "Daily Report" in report
        assert "claude" in report
        assert "✅" in report

    def test_empty_day(self, components):
        exporter, _ = components
        report = exporter.daily_report("2020-01-01")
        assert "No tasks" in report


class TestWeeklyReport:
    def test_markdown(self, components):
        exporter, _ = components
        report = exporter.weekly_report()
        assert "Weekly Report" in report
        assert "Per Agent" in report
        assert "claude" in report

    def test_contains_stats(self, components):
        exporter, _ = components
        report = exporter.weekly_report()
        assert "Success Rate" in report


class TestCSVExport:
    def test_csv(self, components):
        exporter, _ = components
        csv = exporter.tasks_csv()
        assert "id,agent_id,title" in csv
        lines = csv.strip().split("\n")
        assert len(lines) == 5  # header + 4 tasks

    def test_csv_filtered(self, components):
        exporter, _ = components
        csv = exporter.tasks_csv(agent_id="codex")
        lines = csv.strip().split("\n")
        assert len(lines) == 2  # header + 1 codex task


class TestJSONExport:
    def test_json(self, components):
        import json as j
        exporter, _ = components
        data = j.loads(exporter.tasks_json())
        assert len(data) == 4

    def test_json_filtered(self, components):
        import json as j
        exporter, _ = components
        data = j.loads(exporter.tasks_json(agent_id="claude"))
        assert len(data) == 3
