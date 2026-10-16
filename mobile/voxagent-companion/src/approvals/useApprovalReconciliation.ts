import { useCallback, useEffect, useRef, useState } from "react";
import { AppState } from "react-native";

import {
  createApprovalController,
  type ApprovalController,
} from "./controller";
import { initialApprovalState } from "./state";

const APPROVAL_POLL_INTERVAL_MS = 3_000;

export function useApprovalReconciliation(active: boolean) {
  const [state, setState] = useState(initialApprovalState);
  const [appIsActive, setAppIsActive] = useState(
    () =>
      AppState.currentState !== "background" &&
      AppState.currentState !== "inactive"
  );
  const mountedRef = useRef(true);
  const controllerRef = useRef<ApprovalController | null>(null);

  if (!controllerRef.current) {
    controllerRef.current = createApprovalController({
      onStateChange: (nextState) => {
        if (mountedRef.current) setState(nextState);
      },
    });
  }

  const refresh = useCallback(
    () => controllerRef.current?.refresh() ?? Promise.resolve(),
    []
  );
  const decide = useCallback(
    (approvalId: string, approved: boolean) =>
      controllerRef.current?.decide(approvalId, approved) ?? Promise.resolve(),
    []
  );

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (nextState) => {
      const isActive = nextState === "active";
      setAppIsActive(isActive);
      if (active && isActive) void refresh();
    });
    return () => subscription.remove();
  }, [active, refresh]);

  useEffect(() => {
    if (!active || !appIsActive) return;
    void refresh();
    const timer = setInterval(() => {
      controllerRef.current?.expire();
      void refresh();
    }, APPROVAL_POLL_INTERVAL_MS);
    return () => {
      clearInterval(timer);
    };
  }, [active, appIsActive, refresh]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  return { state, refresh, decide };
}
