"""Tests for engine.audit_trail — tamper-evident security logging."""

import pytest
from engine.audit_trail import AuditTrail
from engine.store import StoreManager


@pytest.fixture
def components(tmp_path):
    store = StoreManager(tmp_path)
    store.connect()
    audit = AuditTrail(store)
    yield audit, store
    store.close()


class TestAuditTrail:
    def test_log_permission(self, components):
        audit, store = components
        audit.log_permission_check("claude", "bash", "auto", True)
        rows = store._activity_db.execute(
            "SELECT * FROM activity_log WHERE action = 'permission:check'"
        ).fetchall()
        assert len(rows) == 1

    def test_log_tool_execution(self, components):
        audit, store = components
        audit.log_tool_execution("claude", "read", True, 50)
        rows = store._activity_db.execute(
            "SELECT * FROM activity_log WHERE action = 'tool:execute'"
        ).fetchall()
        assert len(rows) == 1

    def test_log_state_change(self, components):
        audit, _ = components
        audit.log_task_state_change("task-1", "draft", "running")
        # No crash = pass

    def test_log_config_change(self, components):
        audit, _ = components
        audit.log_config_change("permission_mode", "default", "auto")

    def test_log_security_event(self, components):
        audit, _ = components
        audit.log_security_event("blocked_command", "rm -rf / attempted", "critical")

    def test_checksum_chain(self, components):
        audit, _ = components
        audit.log_permission_check("a", "bash", "auto", True)
        audit.log_permission_check("b", "write", "default", False)
        # Checksums should chain
        assert audit._last_checksum != "genesis"

    def test_verify_chain(self, components):
        audit, _ = components
        audit.log_permission_check("claude", "read", "plan", True)
        audit.log_tool_execution("claude", "read", True)
        valid, count = audit.verify_chain()
        assert valid
        assert count == 2

    def test_empty_verify(self, components):
        audit, _ = components
        valid, count = audit.verify_chain()
        assert valid
        assert count == 0
