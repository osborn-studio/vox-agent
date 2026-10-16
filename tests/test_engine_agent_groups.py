"""Tests for engine.agent_groups — team organization."""

import pytest
from engine.agent_groups import GroupManager, AgentGroup


@pytest.fixture
def mgr(tmp_path):
    return GroupManager(tmp_path / "groups.json")


class TestGroupManager:
    def test_create(self, mgr):
        mgr.create(AgentGroup(id="dev", name="Dev Team", members=["claude", "codex"]))
        assert mgr.get("dev") is not None
        assert len(mgr.list_all()) == 1

    def test_delete(self, mgr):
        mgr.create(AgentGroup(id="temp", name="Temp"))
        assert mgr.delete("temp")
        assert mgr.get("temp") is None

    def test_add_member(self, mgr):
        mgr.create(AgentGroup(id="team", name="Team"))
        mgr.add_member("team", "claude")
        mgr.add_member("team", "codex")
        assert mgr.get("team").members == ["claude", "codex"]

    def test_no_duplicate_members(self, mgr):
        mgr.create(AgentGroup(id="team", name="Team"))
        mgr.add_member("team", "claude")
        mgr.add_member("team", "claude")
        assert len(mgr.get("team").members) == 1

    def test_remove_member(self, mgr):
        mgr.create(AgentGroup(id="team", name="Team", members=["a", "b", "c"]))
        mgr.remove_member("team", "b")
        assert mgr.get("team").members == ["a", "c"]

    def test_round_robin(self, mgr):
        mgr.create(AgentGroup(id="rr", name="RR", members=["a", "b", "c"], strategy="round_robin"))
        assert mgr.next_agent("rr") == "a"
        assert mgr.next_agent("rr") == "b"
        assert mgr.next_agent("rr") == "c"
        assert mgr.next_agent("rr") == "a"  # wraps around

    def test_broadcast(self, mgr):
        mgr.create(AgentGroup(id="bc", name="BC", members=["a", "b"], strategy="broadcast"))
        assert mgr.next_agent("bc") is None  # broadcast returns None
        assert mgr.broadcast_targets("bc") == ["a", "b"]

    def test_persistence(self, tmp_path):
        path = tmp_path / "groups.json"
        m1 = GroupManager(path)
        m1.create(AgentGroup(id="saved", name="Saved", members=["claude"]))
        m2 = GroupManager(path)
        assert m2.get("saved") is not None
        assert m2.get("saved").members == ["claude"]

    def test_empty(self, mgr):
        assert mgr.list_all() == []
        assert mgr.get("nope") is None
        assert mgr.next_agent("nope") is None
