"""Tests for engine.capabilities — agent capability matrix."""

import pytest
from engine.capabilities import CapabilityManager, AgentCapability


@pytest.fixture
def mgr():
    return CapabilityManager()


class TestCapabilities:
    def test_default_claude(self, mgr):
        cap = mgr.get("claude")
        assert "bash" in cap.allowed_tools
        assert cap.can_execute_shell
        assert cap.can_write_files

    def test_review_only(self, mgr):
        cap = mgr.get("review-only")
        assert "bash" not in cap.allowed_tools
        assert not cap.can_execute_shell
        assert not cap.can_write_files

    def test_unknown_falls_to_claude(self, mgr):
        cap = mgr.get("unknown-agent")
        assert cap.can_execute_shell  # claude default

    def test_tool_check(self, mgr):
        assert mgr.is_tool_allowed("claude", "bash")
        assert not mgr.is_tool_allowed("review-only", "bash")
        assert mgr.is_tool_allowed("review-only", "read")

    def test_denied_tools(self, mgr):
        mgr.set("restricted", AgentCapability(
            agent_id="restricted",
            allowed_tools={"read", "write", "bash"},
            denied_tools={"bash"},
        ))
        assert not mgr.is_tool_allowed("restricted", "bash")
        assert mgr.is_tool_allowed("restricted", "read")

    def test_custom_capability(self, mgr):
        mgr.set("custom", AgentCapability(
            agent_id="custom",
            allowed_tools={"read"},
            can_execute_shell=False,
            can_write_files=False,
            can_access_network=False,
        ))
        assert not mgr.can_shell("custom")
        assert not mgr.can_write("custom")
        assert not mgr.can_network("custom")

    def test_summary(self, mgr):
        s = mgr.summary("claude")
        assert "tools" in s
        assert s["shell"] is True

    def test_list_all(self, mgr):
        caps = mgr.list_all()
        assert len(caps) >= 3  # claude, codex, review-only
