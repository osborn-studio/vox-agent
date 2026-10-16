export type ConversationVoicePhase =
  | "idle"
  | "starting"
  | "listening"
  | "stopping"
  | "finalizing"
  | "cancelling"
  | "reconnecting"
  | "reconciling"
  | "failed";

export type ConversationVoiceRetryPhase = "starting" | "finalizing";

export interface ConversationVoiceState {
  phase: ConversationVoicePhase;
  error: string | null;
  retriable: boolean;
  retriesUsed: number;
  retryPhase: ConversationVoiceRetryPhase | null;
}

export const initialConversationVoiceState: ConversationVoiceState = {
  phase: "idle",
  error: null,
  retriable: false,
  retriesUsed: 0,
  retryPhase: null,
};

export type ConversationVoiceAction =
  | { type: "BEGIN" }
  | { type: "STARTED" }
  | { type: "STOP" }
  | { type: "FINALIZE" }
  | { type: "CANCEL" }
  | { type: "RECONNECT" }
  | { type: "RECONCILE" }
  | { type: "RESUME" }
  | { type: "COMPLETE" | "RESET" }
  | {
      type: "FAIL";
      error: string;
      retriable: boolean;
      retryPhase: ConversationVoiceRetryPhase;
    }
  | { type: "RETRY" };

const MAX_RETRIES = 1;

export function conversationVoiceReducer(
  state: ConversationVoiceState,
  action: ConversationVoiceAction
): ConversationVoiceState {
  switch (action.type) {
    case "BEGIN":
      return state.phase === "idle"
        ? { ...initialConversationVoiceState, phase: "starting" }
        : state;
    case "STARTED":
      return state.phase === "starting"
        ? { ...state, phase: "listening", error: null }
        : state;
    case "STOP":
      return state.phase === "listening" || state.phase === "reconnecting"
        ? { ...state, phase: "stopping", error: null }
        : state;
    case "FINALIZE":
      return state.phase === "stopping"
        ? { ...state, phase: "finalizing", error: null }
        : state;
    case "CANCEL":
      return canCancelConversationVoice(state)
        ? { ...state, phase: "cancelling", error: null }
        : state;
    case "RECONNECT":
      return state.phase === "listening"
        ? { ...state, phase: "reconnecting" }
        : state;
    case "RECONCILE":
      return state.phase === "idle"
        ? { ...state, phase: "reconciling", error: null }
        : state;
    case "RESUME":
      return state.phase === "reconnecting"
        ? { ...state, phase: "listening", error: null }
        : state;
    case "FAIL": {
      const retriable =
        action.retriable && state.retriesUsed < MAX_RETRIES;
      return {
        ...state,
        phase: "failed",
        error: action.error,
        retriable,
        retryPhase: retriable ? action.retryPhase : null,
      };
    }
    case "RETRY":
      return canRetryConversationVoice(state) && state.retryPhase
        ? {
            ...state,
            phase: state.retryPhase,
            error: null,
            retriable: false,
            retriesUsed: state.retriesUsed + 1,
            retryPhase: null,
          }
        : state;
    case "COMPLETE":
    case "RESET":
      return initialConversationVoiceState;
  }
}

export function canStartConversationVoice(
  state: ConversationVoiceState
): boolean {
  return state.phase === "idle";
}

export function canStopConversationVoice(
  state: ConversationVoiceState
): boolean {
  return state.phase === "listening" || state.phase === "reconnecting";
}

export function canCancelConversationVoice(
  state: ConversationVoiceState
): boolean {
  return [
    "starting",
    "listening",
    "stopping",
    "finalizing",
    "reconnecting",
  ].includes(state.phase);
}

export function canRetryConversationVoice(
  state: ConversationVoiceState
): boolean {
  return (
    state.phase === "failed" &&
    state.retriable &&
    state.retryPhase !== null &&
    state.retriesUsed < MAX_RETRIES
  );
}

export function presentConversationVoice(state: ConversationVoiceState): {
  symbol: string;
  status: string;
  tone: "idle" | "active" | "working" | "error";
} {
  switch (state.phase) {
    case "idle":
      return { symbol: "○", status: "Ready to speak", tone: "idle" };
    case "starting":
      return {
        symbol: "…",
        status: "Requesting microphone access",
        tone: "working",
      };
    case "listening":
      return { symbol: "●", status: "Listening", tone: "active" };
    case "stopping":
      return { symbol: "■", status: "Finishing audio", tone: "working" };
    case "finalizing":
      return {
        symbol: "↻",
        status: "Translating and publishing",
        tone: "working",
      };
    case "cancelling":
      return { symbol: "×", status: "Cancelling speech", tone: "working" };
    case "reconnecting":
      return { symbol: "↻", status: "Reconnecting speech", tone: "working" };
    case "reconciling":
      return {
        symbol: "↻",
        status: "Checking conversation state",
        tone: "working",
      };
    case "failed":
      return {
        symbol: "!",
        status: state.error ?? "Speech failed",
        tone: "error",
      };
  }
}
