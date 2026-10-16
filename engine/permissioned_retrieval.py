"""Permission-filtered retrieval for organization work events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .org_models import RequestContext
from .policy import PolicyEngine
from .work_events import WorkEventStore


@dataclass
class PermissionedRetriever:
    """Searches work events, then filters every candidate through policy."""

    event_store: WorkEventStore
    policy: PolicyEngine
    candidate_limit: int = 100

    def search(self, ctx: RequestContext, query: str, limit: int = 20) -> list[dict[str, Any]]:
        normalized_query = query.casefold().strip()
        if not normalized_query:
            return []

        candidates = self.event_store.list_work_events(
            org_id=ctx.org_id,
            team_id=ctx.team_id,
            project_id=ctx.project_id,
            limit=self.candidate_limit,
        )

        results: list[dict[str, Any]] = []
        for event in candidates:
            if normalized_query not in event["body"].casefold():
                continue

            decision = self.policy.can_read_event(ctx, event)
            if not decision.allowed:
                continue

            results.append(
                {
                    "event_id": event["id"],
                    "source_id": event["id"],
                    "body": event["body"],
                    "source_type": event["source_type"],
                    "visibility": event["visibility"],
                    "created_at": event["created_at"],
                }
            )
            if len(results) >= limit:
                break

        return results
