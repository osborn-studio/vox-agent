import { describe, expect, it, vi } from "vitest";

import type {
  PermissionDecision,
  PermissionRequest,
  ReadResult,
  WriteResult,
} from "../api/client";
import { createApprovalController } from "./controller";

const NOW = new Date("2026-07-26T12:00:00Z");
const REQUEST: PermissionRequest = {
  approval_id: "approval-1",
  task_id: "task-1",
  agent_id: "agent-1",
  tool: "write",
  scope: { action: "write", path: "/tmp/report.md" },
  requested_at: "2026-07-26T11:59:00Z",
  expires_at: "2026-07-26T12:01:00Z",
};

const live = (
  data: PermissionRequest[]
): ReadResult<PermissionRequest[]> => ({
  ok: true,
  offline: false,
  source: "live",
  data,
});

const decided = (
  decision: "approved" | "denied"
): WriteResult<PermissionDecision> => ({
  ok: true,
  offline: false,
  source: "live",
  data: { ok: true, decision },
});

describe("createApprovalController", () => {
  it("reconciles after offline recovery and deduplicates an in-flight decision", async () => {
    const list = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        offline: true,
        code: "ENGINE_OFFLINE",
      })
      .mockResolvedValueOnce(live([REQUEST]));
    let release!: (value: WriteResult<PermissionDecision>) => void;
    const decisionResult = new Promise<WriteResult<PermissionDecision>>(
      (resolve) => {
        release = resolve;
      }
    );
    const decide = vi.fn(() => decisionResult);
    const controller = createApprovalController({
      list,
      decide,
      now: () => NOW,
    });

    await controller.refresh();
    expect(controller.getState().sync).toBe("offline");
    await controller.refresh();
    expect(controller.getState().items[0].phase).toBe("pending");

    const first = controller.decide("approval-1", true);
    const duplicate = controller.decide("approval-1", true);
    expect(first).toBe(duplicate);
    expect(decide).toHaveBeenCalledTimes(1);
    expect(controller.getState().items[0].phase).toBe("resolving");

    release(decided("approved"));
    await first;
    expect(controller.getState().items[0].phase).toBe("approved");
  });

  it("maps a uniform 404 to closed and refreshes without retrying the decision", async () => {
    const list = vi
      .fn()
      .mockResolvedValueOnce(live([REQUEST]))
      .mockResolvedValueOnce(live([]));
    const decide = vi.fn(async () => ({
      ok: false,
      offline: false,
      status: 404,
      code: "REQUEST_REJECTED",
    }) satisfies WriteResult<PermissionDecision>);
    const controller = createApprovalController({
      list,
      decide,
      now: () => NOW,
    });

    await controller.refresh();
    await controller.decide("approval-1", false);

    expect(decide).toHaveBeenCalledTimes(1);
    expect(list).toHaveBeenCalledTimes(2);
    expect(controller.getState().items[0].phase).toBe("closed");
  });

  it("never sends an expired approval or an approval with redacted scope", async () => {
    const expired = { ...REQUEST, expires_at: "2026-07-26T11:59:59Z" };
    const redacted = {
      ...REQUEST,
      approval_id: "approval-2",
      scope: {
        action: "write",
        path: "[redacted: sensitive value]",
      },
    };
    const list = vi.fn(async () => live([expired, redacted]));
    const decide = vi.fn(async () => decided("approved"));
    const controller = createApprovalController({
      list,
      decide,
      now: () => NOW,
    });

    await controller.refresh();
    await controller.decide(expired.approval_id, true);
    await controller.decide(redacted.approval_id, true);
    expect(decide).not.toHaveBeenCalled();

    await controller.decide(redacted.approval_id, false);
    expect(decide).toHaveBeenCalledWith(redacted.approval_id, false);
  });
});
