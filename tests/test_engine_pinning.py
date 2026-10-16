"""Tests for engine.pinning — task pinning."""

import pytest
from engine.pinning import PinManager


@pytest.fixture
def mgr(tmp_path):
    return PinManager(tmp_path)


def test_default_pin_directory_uses_vox_agent_namespace(monkeypatch, tmp_path):
    canonical = tmp_path / "Vox Agent"
    monkeypatch.setenv("VOXAGENT_DATA_DIR", str(canonical))

    assert PinManager().config_dir == canonical


class TestPinning:
    def test_pin(self, mgr):
        mgr.pin("t1")
        assert mgr.is_pinned("t1")

    def test_unpin(self, mgr):
        mgr.pin("t1")
        mgr.unpin("t1")
        assert not mgr.is_pinned("t1")

    def test_toggle(self, mgr):
        assert mgr.toggle("t1") is True
        assert mgr.toggle("t1") is False

    def test_list(self, mgr):
        mgr.pin("b")
        mgr.pin("a")
        assert mgr.list_pinned() == ["a", "b"]

    def test_count(self, mgr):
        assert mgr.count() == 0
        mgr.pin("t1")
        mgr.pin("t2")
        assert mgr.count() == 2

    def test_clear(self, mgr):
        mgr.pin("t1")
        mgr.pin("t2")
        assert mgr.clear() == 2
        assert mgr.count() == 0

    def test_persistence(self, tmp_path):
        m1 = PinManager(tmp_path)
        m1.pin("saved")
        m2 = PinManager(tmp_path)
        assert m2.is_pinned("saved")

    def test_not_pinned(self, mgr):
        assert not mgr.is_pinned("nope")

    def test_no_duplicates(self, mgr):
        mgr.pin("t1")
        mgr.pin("t1")
        assert mgr.count() == 1
