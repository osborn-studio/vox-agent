import { describe, expect, it } from "vitest";

import type { PermissionRequest } from "../api/client";
import {
  approvalReducer,
  canResolveApproval,
  initialApprovalState,
  type ApprovalState,
} from "./state";

const NOW = "2026-07-26T12:00:00Z";

function request(
  approvalId: string,
  overrides: Partial<PermissionRequest> = {}
): PermissionRequest {
  return {
    approval_id: approvalId,
    task_id: "task-1",
    agent_id: "agent-1",
    tool: "write",
    scope: { action: "write", path: "/tmp/report.md" },
    requested_at: "2026-07-26T11:59:00Z",
    expires_at: "2026-07-26T12:01:00Z",
    ...overrides,
  };
}

function synced(...requests: PermissionRequest[]): ApprovalState {
  return approvalReducer(initialApprovalState, {
    type: "SYNC_SUCCESS",
    requests,
    now: NOW,
  });
}

describe("approvalReducer", () => {
  it("reconciles pending requests, preserves terminal audit rows, and closes missing work", () => {
    let state = synced(request("pending"), request("missing"));
    state = approvalReducer(state, {
      type: "DECIDE_START",
      approvalId: "pending",
    });
    expect(
      state.items.find((item) => item.request.approval_id === "pending")?.phase
    ).toBe("resolving");

    state = approvalReducer(state, {
      type: "DECIDE_SUCCESS",
      approvalId: "pending",
      decision: "approved",
      now: "2026-07-26T12:00:05Z",
    });
    state = approvalReducer(state, {
      type: "SYNC_SUCCESS",
      requests: [request("pending")],
      now: "2026-07-26T12:00:10Z",
    });

    expect(
      Object.fromEntries(
        state.items.map((item) => [item.request.approval_id, item.phase])
      )
    ).toEqual({ pending: "approved", missing: "closed" });
    expect(state.sync).toBe("live");
  });

  it("records denied, expired, offline, and unavailable states in text-bearing phases", () => {
    let state = synced(request("deny"), request("expire"));
    state = approvalReducer(state, {
      type: "DECIDE_START",
      approvalId: "deny",
    });
    state = approvalReducer(state, {
      type: "DECIDE_SUCCESS",
      approvalId: "deny",
      decision: "denied",
      now: NOW,
    });
    state = approvalReducer(state, {
      type: "EXPIRE",
      now: "2026-07-26T12:02:00Z",
    });
    expect(
      Object.fromEntries(
        state.items.map((item) => [item.request.approval_id, item.phase])
      )
    ).toEqual({ deny: "denied", expire: "expired" });

    state = approvalReducer(state, {
      type: "SYNC_FAILURE",
      offline: true,
    });
    expect(state.sync).toBe("offline");
    state = approvalReducer(state, {
      type: "SYNC_FAILURE",
      offline: false,
    });
    expect(state.sync).toBe("unavailable");
  });

  it("reopens a failed decision only for another explicit review", () => {
    let state = synced(request("retry"));
    state = approvalReducer(state, {
      type: "DECIDE_START",
      approvalId: "retry",
    });
    state = approvalReducer(state, {
      type: "DECIDE_FAILURE",
      approvalId: "retry",
      offline: true,
      error: "Decision was not sent.",
    });
    expect(state.items[0]).toMatchObject({
      phase: "pending",
      resolutionError: "Decision was not sent.",
    });
    expect(canResolveApproval(state.items[0], state.sync, NOW)).toBe(false);
  });
});
