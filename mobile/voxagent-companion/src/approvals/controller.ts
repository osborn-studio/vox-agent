import {
  decidePermission,
  listPendingPermissions,
  type PermissionDecision,
  type PermissionRequest,
  type ReadResult,
  type WriteResult,
} from "../api/client";
import {
  approvalReducer,
  canResolveApproval,
  initialApprovalState,
  type ApprovalAction,
  type ApprovalState,
} from "./state";
import { presentApproval } from "./presentation";

export interface ApprovalController {
  getState(): ApprovalState;
  refresh(): Promise<void>;
  decide(approvalId: string, approved: boolean): Promise<void>;
  expire(): void;
}

export interface ApprovalControllerOptions {
  list?: () => Promise<ReadResult<PermissionRequest[]>>;
  decide?: (
    approvalId: string,
    approved: boolean
  ) => Promise<WriteResult<PermissionDecision>>;
  now?: () => Date;
  onStateChange?: (state: ApprovalState) => void;
}

export function createApprovalController(
  options: ApprovalControllerOptions = {}
): ApprovalController {
  let state = initialApprovalState;
  let refreshInFlight: Promise<void> | null = null;
  const decisionInFlight = new Map<string, Promise<void>>();
  const read = options.list ?? listPendingPermissions;
  const write = options.decide ?? decidePermission;
  const now = () => (options.now ?? (() => new Date()))().toISOString();

  const dispatch = (action: ApprovalAction) => {
    const next = approvalReducer(state, action);
    if (next === state) return;
    state = next;
    options.onStateChange?.(state);
  };

  const refresh = (): Promise<void> => {
    if (refreshInFlight) return refreshInFlight;
    dispatch({ type: "SYNC_START" });
    let tracked: Promise<void>;
    tracked = read()
      .then((result) => {
        if (result.ok) {
          dispatch({
            type: "SYNC_SUCCESS",
            requests: result.data,
            now: now(),
          });
        } else {
          dispatch({ type: "SYNC_FAILURE", offline: result.offline });
        }
      })
      .catch(() => {
        dispatch({ type: "SYNC_FAILURE", offline: true });
      })
      .finally(() => {
        if (refreshInFlight === tracked) refreshInFlight = null;
      });
    refreshInFlight = tracked;
    return tracked;
  };

  const controller: ApprovalController = {
    getState() {
      return state;
    },
    refresh,
    decide(approvalId, approved) {
      const existing = decisionInFlight.get(approvalId);
      if (existing) return existing;
      const currentTime = now();
      dispatch({ type: "EXPIRE", now: currentTime });
      const item = state.items.find(
        (candidate) => candidate.request.approval_id === approvalId
      );
      const presentation = item ? presentApproval(item.request) : null;
      if (
        !item ||
        !canResolveApproval(item, state.sync, currentTime) ||
        (approved &&
          (!presentation?.hasCompleteScope ||
            !presentation.hasCompleteIdentity))
      ) {
        return Promise.resolve();
      }
      dispatch({ type: "DECIDE_START", approvalId });
      let tracked: Promise<void>;
      tracked = write(approvalId, approved)
        .then(async (result) => {
          if (result.ok) {
            dispatch({
              type: "DECIDE_SUCCESS",
              approvalId,
              decision: result.data.decision,
              now: now(),
            });
            return;
          }
          if (result.status === 404) {
            dispatch({ type: "DECIDE_MISSING", approvalId, now: now() });
            await refresh();
            return;
          }
          dispatch({
            type: "DECIDE_FAILURE",
            approvalId,
            offline: result.offline,
            error: result.offline
              ? "Decision was not sent. Reconnect and review again."
              : "Decision was not accepted. Review the current request.",
          });
        })
        .catch(() => {
          dispatch({
            type: "DECIDE_FAILURE",
            approvalId,
            offline: true,
            error: "Decision was not sent. Reconnect and review again.",
          });
        })
        .finally(() => {
          if (decisionInFlight.get(approvalId) === tracked) {
            decisionInFlight.delete(approvalId);
          }
        });
      decisionInFlight.set(approvalId, tracked);
      return tracked;
    },
    expire() {
      dispatch({ type: "EXPIRE", now: now() });
    },
  };
  return controller;
}
