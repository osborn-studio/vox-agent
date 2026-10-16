import { describe, expect, it } from "vitest";

import {
  conversationReducer,
  initialConversationState,
  parseConversationInvite,
  remainingConversationSeconds,
} from "./state";
import type {
  ConversationLiveDraft,
  ConversationTurn,
} from "../api/client";

const session = {
  schema: "voxagent.conversation.session" as const,
  schemaVersion: 1 as const,
  id: "conv_123",
  mode: "REMOTE_QR" as const,
  status: "ACTIVE" as const,
  hostLanguage: "en",
  guestLanguage: "zh-CN",
  participantCount: 2,
  createdAt: "2026-07-19T12:00:00Z",
  expiresAt: "2026-07-19T13:00:00Z",
  nextSequence: 1,
};

const hostDraft: ConversationLiveDraft = {
  schema: "voxagent.conversation.live",
  schemaVersion: 1,
  type: "conversation.live",
  sessionId: session.id,
  participantRole: "HOST",
  streamId: "stream-1",
  segmentId: "segment-1",
  revision: 2,
  sourceLanguage: "en",
  targetLanguage: "zh-CN",
  speechEvent: {
    schema: "voxagent.speech.event",
    schemaVersion: 1,
    eventId: "partial-2",
    type: "transcript.partial",
    streamId: "stream-1",
    sessionId: session.id,
    sequence: 2,
    monotonicMs: 800,
    emittedAt: "2026-07-19T12:00:01Z",
    payload: {
      segmentId: "segment-1",
      revision: 2,
      text: "Hello there",
      language: { code: "en", confidence: 0.98 },
      speaker: { id: "host", label: "Host" },
      audioStartMs: 0,
      audioEndMs: 800,
      confidence: 0.96,
    },
  },
  updatedAt: "2026-07-19T12:00:01Z",
};

describe("conversation invite parser", () => {
  it("accepts only the opaque v1 fragment contract", () => {
    expect(
      parseConversationInvite(
        "voxagent://conversation/join#v=1&token=invite-token-with-enough-entropy-123"
      )
    ).toBe("invite-token-with-enough-entropy-123");
  });

  it("rejects identity, authority, query, version, and duplicate fields", () => {
    expect(
      parseConversationInvite(
        "voxagent://conversation/join?user=host#v=1&token=invite-token-with-enough-entropy-123"
      )
    ).toBeNull();
    expect(
      parseConversationInvite(
        "voxagent://conversation/join#v=2&token=invite-token-with-enough-entropy-123"
      )
    ).toBeNull();
    expect(
      parseConversationInvite(
        "voxagent://conversation/join#v=1&token=invite-token-with-enough-entropy-123&token=duplicate"
      )
    ).toBeNull();
    expect(parseConversationInvite("https://backend.invalid/admin")).toBeNull();
  });
});

describe("conversation client state", () => {
  it("moves through join, reconnect, replay, and expiry without storing a token", () => {
    const joined = conversationReducer(initialConversationState, {
      type: "JOINED",
      session,
    });
    const reconnecting = conversationReducer(joined, {
      type: "REPLAY_FAILED",
      error: "Network interrupted",
    });
    const replayed = conversationReducer(reconnecting, {
      type: "REPLAYED",
      status: "ACTIVE",
      turns: [],
      nextSequence: 1,
    });
    const expired = conversationReducer(replayed, { type: "EXPIRED" });

    expect(joined.connection).toBe("ACTIVE");
    expect(reconnecting.connection).toBe("RECONNECTING");
    expect(replayed.connection).toBe("ACTIVE");
    expect(expired.connection).toBe("EXPIRED");
    expect(JSON.stringify(expired)).not.toContain("AccessToken");
    expect(JSON.stringify(expired)).not.toContain("invite-token");
  });

  it("computes a clamped visible expiry countdown", () => {
    expect(
      remainingConversationSeconds(
        "2026-07-19T12:00:10Z",
        Date.parse("2026-07-19T12:00:00Z")
      )
    ).toBe(10);
    expect(
      remainingConversationSeconds(
        "2026-07-19T11:59:00Z",
        Date.parse("2026-07-19T12:00:00Z")
      )
    ).toBe(0);
  });

  it("keeps an active session retryable when a host operation fails", () => {
    const active = conversationReducer(initialConversationState, {
      type: "CREATED",
      session,
    });

    const failed = conversationReducer(active, {
      type: "OPERATION_FAILED",
      error: "Could not end the conversation.",
    });

    expect(failed.connection).toBe("ACTIVE");
    expect(failed.session).toBe(session);
    expect(failed.error).toBe("Could not end the conversation.");
  });

  it("keeps one monotonic draft per role and converges it into the matching final", () => {
    const active = conversationReducer(initialConversationState, {
      type: "CREATED",
      session,
    });
    const withDraft = conversationReducer(active, {
      type: "LIVE_RECEIVED",
      snapshot: {
        sessionId: session.id,
        host: hostDraft,
        guest: null,
        status: "ACTIVE",
      },
    });
    const afterStaleRevision = conversationReducer(withDraft, {
      type: "LIVE_RECEIVED",
      snapshot: {
        sessionId: session.id,
        host: {
          ...hostDraft,
          revision: 1,
          speechEvent: {
            ...hostDraft.speechEvent,
            payload: { ...hostDraft.speechEvent.payload, revision: 1 },
          },
        },
        guest: null,
        status: "ACTIVE",
      },
    });
    const matchingFinal = {
      sequence: 1,
      participantRole: "HOST",
      speechEvent: {
        streamId: hostDraft.streamId,
        payload: { segmentId: hostDraft.segmentId },
      },
    } as ConversationTurn;
    const converged = conversationReducer(afterStaleRevision, {
      type: "REPLAYED",
      status: "ACTIVE",
      turns: [matchingFinal],
      nextSequence: 2,
    });
    const afterLateDraft = conversationReducer(converged, {
      type: "LIVE_RECEIVED",
      snapshot: {
        sessionId: session.id,
        host: hostDraft,
        guest: null,
        status: "ACTIVE",
      },
    });

    expect(withDraft.liveDrafts.HOST?.revision).toBe(2);
    expect(afterStaleRevision.liveDrafts.HOST?.revision).toBe(2);
    expect(converged.liveDrafts.HOST).toBeNull();
    expect(converged.turns).toEqual([matchingFinal]);
    expect(afterLateDraft.liveDrafts.HOST).toBeNull();
  });

  it("drops best-effort drafts on reconnect, expiry, and reset", () => {
    const active = conversationReducer(initialConversationState, {
      type: "CREATED",
      session,
    });
    const withDraft = conversationReducer(active, {
      type: "LIVE_RECEIVED",
      snapshot: {
        sessionId: session.id,
        host: hostDraft,
        guest: null,
        status: "ACTIVE",
      },
    });

    expect(
      conversationReducer(withDraft, { type: "REPLAYING" }).liveDrafts.HOST
    ).toBeNull();
    expect(
      conversationReducer(withDraft, {
        type: "REPLAY_FAILED",
        error: "Network interrupted",
      }).liveDrafts.HOST
    ).toBeNull();
    expect(
      conversationReducer(withDraft, { type: "EXPIRED" }).liveDrafts.HOST
    ).toBeNull();
    expect(
      conversationReducer(withDraft, { type: "RESET" }).liveDrafts.HOST
    ).toBeNull();
  });
});
