import type { PermissionRequest } from "../api/client";

export type ApprovalPhase =
  | "pending"
  | "resolving"
  | "approved"
  | "denied"
  | "expired"
  | "closed";

export type ApprovalSync =
  | "idle"
  | "loading"
  | "live"
  | "offline"
  | "unavailable";

export interface ApprovalItem {
  request: PermissionRequest;
  phase: ApprovalPhase;
  resolutionError: string | null;
  resolvedAt: string | null;
}

export interface ApprovalState {
  items: ApprovalItem[];
  sync: ApprovalSync;
  lastSyncedAt: string | null;
}

export const initialApprovalState: ApprovalState = {
  items: [],
  sync: "idle",
  lastSyncedAt: null,
};

export type ApprovalAction =
  | { type: "SYNC_START" }
  | {
      type: "SYNC_SUCCESS";
      requests: PermissionRequest[];
      now: string;
    }
  | { type: "SYNC_FAILURE"; offline: boolean }
  | { type: "DECIDE_START"; approvalId: string }
  | {
      type: "DECIDE_SUCCESS";
      approvalId: string;
      decision: "approved" | "denied";
      now: string;
    }
  | { type: "DECIDE_MISSING"; approvalId: string; now: string }
  | {
      type: "DECIDE_FAILURE";
      approvalId: string;
      offline: boolean;
      error: string;
    }
  | { type: "EXPIRE"; now: string };

const MAX_ITEMS = 100;

function timeValue(value: string): number {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function isExpired(request: PermissionRequest, now: string): boolean {
  return timeValue(request.expires_at) <= timeValue(now);
}

function ordered(items: ApprovalItem[]): ApprovalItem[] {
  return [...items]
    .sort((left, right) => {
      const leftOpen =
        left.phase === "pending" || left.phase === "resolving" ? 0 : 1;
      const rightOpen =
        right.phase === "pending" || right.phase === "resolving" ? 0 : 1;
      if (leftOpen !== rightOpen) return leftOpen - rightOpen;
      if (leftOpen === 1) {
        const resolved =
          timeValue(right.resolvedAt || right.request.expires_at) -
          timeValue(left.resolvedAt || left.request.expires_at);
        if (resolved !== 0) return resolved;
      }
      const deadline =
        timeValue(left.request.expires_at) - timeValue(right.request.expires_at);
      if (deadline !== 0) return deadline;
      return left.request.approval_id.localeCompare(
        right.request.approval_id
      );
    })
    .slice(0, MAX_ITEMS);
}

function updateItem(
  state: ApprovalState,
  approvalId: string,
  transform: (item: ApprovalItem) => ApprovalItem
): ApprovalItem[] {
  return state.items.map((item) =>
    item.request.approval_id === approvalId ? transform(item) : item
  );
}

export function approvalReducer(
  state: ApprovalState,
  action: ApprovalAction
): ApprovalState {
  switch (action.type) {
    case "SYNC_START":
      return { ...state, sync: "loading" };
    case "SYNC_SUCCESS": {
      const incoming = new Map(
        action.requests.map((request) => [request.approval_id, request])
      );
      const retained: ApprovalItem[] = [];
      for (const item of state.items) {
        const request = incoming.get(item.request.approval_id);
        if (request) {
          incoming.delete(item.request.approval_id);
          const isOpen =
            item.phase === "pending" || item.phase === "resolving";
          retained.push({
            ...item,
            request,
            phase: !isOpen
              ? item.phase
              : item.phase === "resolving"
                ? "resolving"
                : isExpired(request, action.now)
                  ? "expired"
                  : "pending",
            resolutionError: null,
          });
          continue;
        }
        if (item.phase === "pending" || item.phase === "resolving") {
          retained.push({
            ...item,
            phase: isExpired(item.request, action.now) ? "expired" : "closed",
            resolutionError: null,
            resolvedAt: action.now,
          });
        } else {
          retained.push(item);
        }
      }
      for (const request of incoming.values()) {
        retained.push({
          request,
          phase: isExpired(request, action.now) ? "expired" : "pending",
          resolutionError: null,
          resolvedAt: null,
        });
      }
      return {
        items: ordered(retained),
        sync: "live",
        lastSyncedAt: action.now,
      };
    }
    case "SYNC_FAILURE":
      return {
        ...state,
        sync: action.offline ? "offline" : "unavailable",
      };
    case "DECIDE_START":
      return {
        ...state,
        items: updateItem(state, action.approvalId, (item) => ({
          ...item,
          phase: item.phase === "pending" ? "resolving" : item.phase,
          resolutionError: null,
        })),
      };
    case "DECIDE_SUCCESS":
      return {
        ...state,
        items: ordered(
          updateItem(state, action.approvalId, (item) => ({
            ...item,
            phase: action.decision,
            resolutionError: null,
            resolvedAt: action.now,
          }))
        ),
      };
    case "DECIDE_MISSING":
      return {
        ...state,
        items: ordered(
          updateItem(state, action.approvalId, (item) => ({
            ...item,
            phase: isExpired(item.request, action.now) ? "expired" : "closed",
            resolutionError: null,
            resolvedAt: action.now,
          }))
        ),
      };
    case "DECIDE_FAILURE":
      return {
        ...state,
        sync: action.offline ? "offline" : state.sync,
        items: updateItem(state, action.approvalId, (item) => ({
          ...item,
          phase: item.phase === "resolving" ? "pending" : item.phase,
          resolutionError: action.error,
        })),
      };
    case "EXPIRE":
      return {
        ...state,
        items: ordered(
          state.items.map((item) =>
            (item.phase === "pending" || item.phase === "resolving") &&
            isExpired(item.request, action.now)
              ? {
                  ...item,
                  phase: "expired" as const,
                  resolutionError: null,
                  resolvedAt: action.now,
                }
              : item
          )
        ),
      };
  }
}

export function canResolveApproval(
  item: ApprovalItem,
  sync: ApprovalSync,
  now: string
): boolean {
  return (
    sync === "live" &&
    item.phase === "pending" &&
    !isExpired(item.request, now)
  );
}
