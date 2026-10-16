import type {
  ConversationParticipantRole,
  ConversationLiveDraft,
  ConversationLiveSnapshot,
  ConversationSession,
  ConversationTurn,
} from "../api/client";

export type ConversationConnection =
  | "IDLE"
  | "STARTING"
  | "WAITING"
  | "ACTIVE"
  | "RECONNECTING"
  | "EXPIRED"
  | "ENDED"
  | "ERROR";

export interface ConversationClientState {
  connection: ConversationConnection;
  role: ConversationParticipantRole | null;
  session: ConversationSession | null;
  turns: ConversationTurn[];
  liveDrafts: Record<
    ConversationParticipantRole,
    ConversationLiveDraft | null
  >;
  nextSequence: number;
  error: string | null;
}

export const initialConversationState: ConversationClientState = {
  connection: "IDLE",
  role: null,
  session: null,
  turns: [],
  liveDrafts: { HOST: null, GUEST: null },
  nextSequence: 1,
  error: null,
};

export type ConversationAction =
  | { type: "RESET" }
  | { type: "STARTING" }
  | {
      type: "CREATED" | "JOINED";
      session: ConversationSession;
    }
  | { type: "REPLAYING" }
  | { type: "LIVE_RECEIVED"; snapshot: ConversationLiveSnapshot }
  | { type: "REPLAY_FAILED"; error: string }
  | {
      type: "REPLAYED";
      status: "WAITING" | "ACTIVE";
      turns: ConversationTurn[];
      nextSequence: number;
    }
  | { type: "EXPIRED" }
  | { type: "ENDED" }
  | { type: "OPERATION_FAILED"; error: string }
  | { type: "FAILED"; error: string };

export function conversationReducer(
  state: ConversationClientState,
  action: ConversationAction
): ConversationClientState {
  switch (action.type) {
    case "RESET":
      return initialConversationState;
    case "STARTING":
      return { ...initialConversationState, connection: "STARTING" };
    case "CREATED":
      return {
        connection: action.session.status === "WAITING" ? "WAITING" : "ACTIVE",
        role: "HOST",
        session: action.session,
        turns: [],
        liveDrafts: { HOST: null, GUEST: null },
        nextSequence: action.session.nextSequence,
        error: null,
      };
    case "JOINED":
      return {
        connection: "ACTIVE",
        role: "GUEST",
        session: action.session,
        turns: [],
        liveDrafts: { HOST: null, GUEST: null },
        nextSequence: action.session.nextSequence,
        error: null,
      };
    case "REPLAYING":
      return state.session
        ? {
            ...state,
            connection: "RECONNECTING",
            liveDrafts: { HOST: null, GUEST: null },
            error: null,
          }
        : state;
    case "REPLAY_FAILED":
      return state.session
        ? {
            ...state,
            connection: "RECONNECTING",
            liveDrafts: { HOST: null, GUEST: null },
            error: action.error,
          }
        : state;
    case "REPLAYED": {
      const turns = new Map(state.turns.map((turn) => [turn.sequence, turn]));
      for (const turn of action.turns) turns.set(turn.sequence, turn);
      const orderedTurns = [...turns.values()].sort(
        (left, right) => left.sequence - right.sequence
      );
      return {
        ...state,
        connection: action.status === "WAITING" ? "WAITING" : "ACTIVE",
        session: state.session
          ? { ...state.session, status: action.status, nextSequence: action.nextSequence }
          : null,
        turns: orderedTurns,
        liveDrafts:
          action.status === "WAITING"
            ? { HOST: null, GUEST: null }
            : {
                HOST: draftAfterFinals(
                  state.liveDrafts.HOST,
                  orderedTurns
                ),
                GUEST: draftAfterFinals(
                  state.liveDrafts.GUEST,
                  orderedTurns
                ),
              },
        nextSequence: action.nextSequence,
        error: null,
      };
    }
    case "LIVE_RECEIVED":
      if (
        !state.session ||
        action.snapshot.sessionId !== state.session.id
      ) {
        return state;
      }
      const hostDraft = mergeLiveDraft(
        state.liveDrafts.HOST,
        action.snapshot.host,
        state.turns
      );
      const guestDraft = mergeLiveDraft(
        state.liveDrafts.GUEST,
        action.snapshot.guest,
        state.turns
      );
      if (
        hostDraft === state.liveDrafts.HOST &&
        guestDraft === state.liveDrafts.GUEST
      ) {
        return state;
      }
      return {
        ...state,
        liveDrafts: {
          HOST: hostDraft,
          GUEST: guestDraft,
        },
      };
    case "EXPIRED":
      return {
        ...state,
        connection: "EXPIRED",
        liveDrafts: { HOST: null, GUEST: null },
        error: null,
      };
    case "ENDED":
      return {
        ...state,
        connection: "ENDED",
        liveDrafts: { HOST: null, GUEST: null },
        error: null,
      };
    case "OPERATION_FAILED":
      return state.session ? { ...state, error: action.error } : state;
    case "FAILED":
      return { ...state, connection: "ERROR", error: action.error };
  }
}

function matchesFinal(
  draft: ConversationLiveDraft,
  turn: ConversationTurn
): boolean {
  return (
    turn.participantRole === draft.participantRole &&
    turn.speechEvent.streamId === draft.streamId &&
    turn.speechEvent.payload.segmentId === draft.segmentId
  );
}

function draftAfterFinals(
  draft: ConversationLiveDraft | null,
  turns: ConversationTurn[]
): ConversationLiveDraft | null {
  return draft && turns.some((turn) => matchesFinal(draft, turn))
    ? null
    : draft;
}

function mergeLiveDraft(
  current: ConversationLiveDraft | null,
  incoming: ConversationLiveDraft | null,
  turns: ConversationTurn[]
): ConversationLiveDraft | null {
  if (incoming === null || turns.some((turn) => matchesFinal(incoming, turn))) {
    return null;
  }
  if (
    current &&
    current.streamId === incoming.streamId &&
    current.segmentId === incoming.segmentId &&
    incoming.revision <= current.revision
  ) {
    return current;
  }
  return incoming;
}

export function parseConversationInvite(payload: string): string | null {
  try {
    const url = new URL(payload.trim());
    if (
      url.protocol !== "voxagent:" ||
      url.hostname !== "conversation" ||
      url.pathname !== "/join" ||
      url.username ||
      url.password ||
      url.port ||
      url.search
    ) {
      return null;
    }
    const fragment = url.hash.startsWith("#") ? url.hash.slice(1) : "";
    const params = new URLSearchParams(fragment);
    if (
      [...params.keys()].some((key) => key !== "v" && key !== "token") ||
      params.getAll("v").length !== 1 ||
      params.getAll("token").length !== 1 ||
      params.get("v") !== "1"
    ) {
      return null;
    }
    const token = params.get("token") || "";
    return token.length >= 20 && token.length <= 512 ? token : null;
  } catch {
    return null;
  }
}

export function remainingConversationSeconds(
  expiresAt: string,
  nowMs: number = Date.now()
): number {
  const expiresMs = Date.parse(expiresAt);
  if (!Number.isFinite(expiresMs)) return 0;
  return Math.max(0, Math.ceil((expiresMs - nowMs) / 1_000));
}
