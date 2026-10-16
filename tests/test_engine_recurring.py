"""Tests for engine.recurring — scheduled and interval task execution."""

import pytest

from engine.recurring import RecurringScheduler, RecurringSchedule


@pytest.fixture
def scheduler(tmp_path):
    return RecurringScheduler(tmp_path / "schedules.json")


class TestScheduleCRUD:
    def test_add_and_list(self, scheduler):
        s = RecurringSchedule(id="test-1", name="Test", agent_id="claude", prompt="hello")
        scheduler.add(s)
        assert len(scheduler.list_all()) == 1

    def test_get(self, scheduler):
        s = RecurringSchedule(id="test-1", name="Test", agent_id="claude", prompt="hello")
        scheduler.add(s)
        assert scheduler.get("test-1") is not None
        assert scheduler.get("nope") is None

    def test_remove(self, scheduler):
        s = RecurringSchedule(id="test-1", name="Test", agent_id="claude", prompt="hello")
        scheduler.add(s)
        assert scheduler.remove("test-1")
        assert len(scheduler.list_all()) == 0

    def test_remove_nonexistent(self, scheduler):
        assert not scheduler.remove("nope")

    def test_enable_disable(self, scheduler):
        s = RecurringSchedule(id="test-1", name="Test", agent_id="claude", prompt="hello")
        scheduler.add(s)
        scheduler.disable("test-1")
        assert not scheduler.get("test-1").enabled
        scheduler.enable("test-1")
        assert scheduler.get("test-1").enabled


class TestPersistence:
    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "schedules.json"
        s1 = RecurringScheduler(path)
        s1.add(RecurringSchedule(id="a", name="A", agent_id="claude", prompt="test", interval_minutes=10))
        s1.add(RecurringSchedule(id="b", name="B", agent_id="codex", prompt="test2", daily_at="02:00"))

        s2 = RecurringScheduler(path)
        assert len(s2.list_all()) == 2
        assert s2.get("a").interval_minutes == 10
        assert s2.get("b").daily_at == "02:00"


class TestExecution:
    @pytest.mark.asyncio
    async def test_trigger_callback(self, scheduler):
        triggered = []

        async def on_trigger(schedule):
            triggered.append(schedule.id)

        scheduler.set_trigger(on_trigger)
        s = RecurringSchedule(id="test", name="Test", agent_id="claude", prompt="hello")
        scheduler.add(s)
        await scheduler._execute(s)

        assert "test" in triggered
        assert s.run_count == 1
        assert s.last_run != ""

    @pytest.mark.asyncio
    async def test_max_runs(self, scheduler):
        triggered = []

        async def on_trigger(schedule):
            triggered.append(1)

        scheduler.set_trigger(on_trigger)
        s = RecurringSchedule(
            id="limited", name="Limited", agent_id="claude",
            prompt="test", max_runs=2,
        )
        scheduler.add(s)

        for _ in range(5):
            if s.max_runs > 0 and s.run_count >= s.max_runs:
                break
            await scheduler._execute(s)

        assert len(triggered) == 2

    @pytest.mark.asyncio
    async def test_disabled_not_triggered(self, scheduler):
        triggered = []

        async def on_trigger(schedule):
            triggered.append(1)

        scheduler.set_trigger(on_trigger)
        s = RecurringSchedule(id="off", name="Off", agent_id="claude", prompt="test", enabled=False)
        scheduler.add(s)

        # check_daily should skip disabled
        await scheduler.check_daily()
        assert len(triggered) == 0

    @pytest.mark.asyncio
    async def test_start_all_counts(self, scheduler):
        scheduler.add(RecurringSchedule(
            id="a", name="A", agent_id="claude", prompt="test", interval_minutes=60,
        ))
        scheduler.add(RecurringSchedule(
            id="b", name="B", agent_id="claude", prompt="test", interval_minutes=30,
        ))
        scheduler.add(RecurringSchedule(
            id="c", name="C", agent_id="claude", prompt="test", daily_at="02:00",
        ))

        count = scheduler.start_all()
        assert count == 2  # only interval-based
        scheduler.stop_all()
