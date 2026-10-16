"""Permission policy for organization work-context access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .access_audit import AccessAuditRecord, AccessAuditStore
from .org_models import RequestContext
from .org_models import OrgRole
from .org_store import OrgStore
from .work_events import WorkEvent, WorkEventVisibility


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str


class PolicyEngine:
    """Evaluates whether a requester can read a work event."""

    def __init__(self, org_store: OrgStore, audit_store: AccessAuditStore) -> None:
        self.org_store = org_store
        self.audit_store = audit_store

    def can_read_event(
        self, ctx: RequestContext, event: WorkEvent | dict[str, Any]
    ) -> PolicyDecision:
        org_id = _event_value(event, "org_id")
        actor_id = _event_value(event, "actor_id")
        visibility = WorkEventVisibility(_event_value(event, "visibility"))
        team_id = _event_value(event, "team_id")
        event_id = _event_value(event, "id")

        if ctx.org_id != org_id:
            return self._decide(
                ctx,
                actor_id,
                event_id,
                visibility.value,
                False,
                "org_mismatch",
            )

        if visibility == WorkEventVisibility.ADMIN_AUDIT:
            role = self.org_store.resolve_user_role(ctx.requester_user_id)
            allowed = role == OrgRole.ADMIN.value
            return self._decide(
                ctx,
                actor_id,
                event_id,
                visibility.value,
                allowed,
                "admin_audit_access" if allowed else "admin_role_required",
            )

        if ctx.requester_user_id == actor_id:
            if visibility == WorkEventVisibility.PRIVATE:
                return PolicyDecision(True, "creator_private_access")
            return PolicyDecision(True, "creator_access")

        if visibility == WorkEventVisibility.PRIVATE:
            return self._decide(
                ctx,
                actor_id,
                event_id,
                visibility.value,
                False,
                "private_event_owner_only",
            )

        if visibility in {
            WorkEventVisibility.TEAM_RAW,
            WorkEventVisibility.TEAM_SUMMARY,
        }:
            if team_id and self.org_store.get_team_role(ctx.requester_user_id, team_id):
                return self._decide(
                    ctx,
                    actor_id,
                    event_id,
                    visibility.value,
                    True,
                    f"{visibility.value}_member_access",
                )
            return self._decide(
                ctx,
                actor_id,
                event_id,
                visibility.value,
                False,
                "team_membership_required",
            )

        if visibility in {
            WorkEventVisibility.PROJECT_RAW,
            WorkEventVisibility.PROJECT_SUMMARY,
        }:
            project_id = _event_value(event, "project_id")
            if project_id and self.org_store.get_project_role(
                ctx.requester_user_id,
                project_id,
            ):
                return self._decide(
                    ctx,
                    actor_id,
                    event_id,
                    visibility.value,
                    True,
                    f"{visibility.value}_member_access",
                )
            return self._decide(
                ctx,
                actor_id,
                event_id,
                visibility.value,
                False,
                "project_membership_required",
            )

        if visibility == WorkEventVisibility.ORG_SUMMARY:
            return self._decide(
                ctx,
                actor_id,
                event_id,
                visibility.value,
                True,
                f"{visibility.value}_access",
            )

        return self._decide(
            ctx,
            actor_id,
            event_id,
            visibility.value,
            False,
            "unsupported_visibility",
        )

    def _decide(
        self,
        ctx: RequestContext,
        target_actor_id: str,
        event_id: str,
        target_scope: str,
        allowed: bool,
        reason: str,
    ) -> PolicyDecision:
        self.audit_store.insert_audit_record(
            AccessAuditRecord(
                org_id=ctx.org_id,
                requester_user_id=ctx.requester_user_id,
                target_actor_id=target_actor_id,
                target_scope=target_scope,
                decision="allowed" if allowed else "denied",
                reason=reason,
                source_ids=[event_id],
            )
        )
        return PolicyDecision(allowed=allowed, reason=reason)


def _event_value(event: WorkEvent | dict[str, Any], key: str) -> Any:
    value = event[key] if isinstance(event, dict) else getattr(event, key)
    if isinstance(value, WorkEventVisibility):
        return value.value
    return value
