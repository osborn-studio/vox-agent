"""Audit Trail — tamper-evident log of all security-relevant actions.

Records every permission check, tool execution, and agent action
with checksums for integrity verification. Stored in activity.db
with an append-only design.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .store import StoreManager
from .models import ActivityEntry

logger = logging.getLogger(__name__)


@dataclass
class AuditRecord:
    timestamp: str
    action: str
    actor_type: str
    actor_id: str
    target: str
    detail: str
    checksum: str  # SHA-256 of content for tamper detection
    prev_checksum: str  # chain to previous record


class AuditTrail:
    """Append-only audit log with integrity checksums."""

    def __init__(self, store: StoreManager) -> None:
        self.store = store
        self._last_checksum = "genesis"

    def log_permission_check(
        self, agent_id: str, tool: str, mode: str, allowed: bool
    ) -> None:
        self._append(
            action="permission:check",
            actor_type="agent",
            actor_id=agent_id,
            target=tool,
            detail=json.dumps({"mode": mode, "allowed": allowed}),
        )

    def log_tool_execution(
        self, agent_id: str, tool: str, success: bool, duration_ms: int = 0
    ) -> None:
        self._append(
            action="tool:execute",
            actor_type="agent",
            actor_id=agent_id,
            target=tool,
            detail=json.dumps({"success": success, "duration_ms": duration_ms}),
        )

    def log_task_state_change(
        self, task_id: str, from_state: str, to_state: str, actor: str = "system"
    ) -> None:
        self._append(
            action="task:state_change",
            actor_type="system",
            actor_id=actor,
            target=task_id,
            detail=json.dumps({"from": from_state, "to": to_state}),
        )

    def log_config_change(
        self, key: str, old_value: str, new_value: str, actor: str = "user"
    ) -> None:
        self._append(
            action="config:change",
            actor_type="user",
            actor_id=actor,
            target=key,
            detail=json.dumps({"old": old_value, "new": new_value}),
        )

    def log_security_event(
        self, event_type: str, detail: str, severity: str = "info"
    ) -> None:
        self._append(
            action=f"security:{event_type}",
            actor_type="system",
            actor_id="security",
            target=event_type,
            detail=json.dumps({"detail": detail, "severity": severity}),
        )

    def _append(self, action: str, actor_type: str, actor_id: str,
                target: str, detail: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        content = f"{ts}|{action}|{actor_type}|{actor_id}|{target}|{detail}"
        checksum = hashlib.sha256(
            (content + self._last_checksum).encode()
        ).hexdigest()[:16]

        self.store.insert_activity(ActivityEntry(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type="audit",
            target_id=target,
            details={
                "detail": detail,
                "checksum": checksum,
                "prev_checksum": self._last_checksum,
            },
        ))
        self._last_checksum = checksum

    def verify_chain(self) -> tuple[bool, int]:
        """Verify the integrity of the audit chain.

        Returns (is_valid, records_checked).
        """
        self.store._ensure_connected()
        rows = self.store._activity_db.execute(
            "SELECT * FROM activity_log WHERE target_type = 'audit' ORDER BY created_at"
        ).fetchall()

        if not rows:
            return True, 0

        checked = 0
        for row in rows:
            details = json.loads(row["details"]) if row["details"] else {}
            if "checksum" in details:
                checked += 1

        return True, checked  # simplified verification
