import assert from "node:assert/strict";
import test from "node:test";

import {
  conversationLiveDraftSchema,
  conversationLiveSnapshotSchema,
  conversationSessionCreatedSchema,
  publishConversationLiveRequestSchema,
  publishConversationTurnRequestSchema
} from "@voxlog/contracts";

import { createApp } from "../app.js";
import type { BackendClient } from "../lib/backend-client.js";

const session = {
  schema: "voxagent.conversation.session",
  schemaVersion: 1,
  id: "conv_123",
  mode: "REMOTE_QR",
  status: "WAITING",
  hostLanguage: "en",
  guestLanguage: "zh-CN",
  participantCount: 1,
  createdAt: "2026-07-19T12:00:00Z",
  expiresAt: "2026-07-19T13:00:00Z",
  nextSequence: 1
} as const;

const speechEvent = {
  schema: "voxagent.speech.event",
  schemaVersion: 1,
  eventId: "speech_event_1",
  streamId: "speech_123",
  sessionId: "conv_123",
  sequence: 2,
  type: "transcript.final",
  monotonicMs: 800,
  emittedAt: "2026-07-19T12:00:01Z",
  payload: {
    segmentId: "segment-1",
    revision: 1,
    text: "hello",
    language: { code: "en", confidence: 0.99 },
    speaker: { id: "speaker-a", label: "A" },
    audioStartMs: 0,
    audioEndMs: 800,
    confidence: 0.96
  }
} as const;

const translationEvent = {
  schema: "voxagent.translation.event",
  schemaVersion: 1,
  eventId: "translation_event_1",
  translationId: "translation_1",
  type: "translation.final",
  emittedAt: "2026-07-19T12:00:02Z",
  latencyMs: 50,
  payload: {
    sourceLanguage: "en",
    targetLanguage: "zh-CN",
    sourceSegments: [
      {
        streamId: "speech_123",
        segmentId: "segment-1",
        revision: 1,
        audioStartMs: 0,
        audioEndMs: 800
      }
    ],
    text: "你好",
    confidence: 0.95,
    provider: "test",
    model: "test-v1",
    glossaryHits: []
  }
} as const;

const turn = {
  schema: "voxagent.conversation.turn",
  schemaVersion: 1,
  type: "conversation.turn",
  eventId: "conversation_event_1",
  sessionId: "conv_123",
  sequence: 1,
  participantRole: "HOST",
  clientEventId: "host-turn-1",
  speechEvent,
  translationEvent,
  createdAt: "2026-07-19T12:00:03Z"
} as const;

const partialSpeechEvent = {
  ...speechEvent,
  eventId: "speech_partial_1",
  type: "transcript.partial",
  payload: {
    ...speechEvent.payload,
    text: "hel"
  }
} as const;

const liveDraft = {
  schema: "voxagent.conversation.live",
  schemaVersion: 1,
  type: "conversation.live",
  sessionId: "conv_123",
  participantRole: "HOST",
  streamId: "speech_123",
  segmentId: "segment-1",
  revision: 1,
  sourceLanguage: "en",
  targetLanguage: "zh-CN",
  speechEvent: partialSpeechEvent,
  updatedAt: "2026-07-19T12:00:01Z"
} as const;

type Call = {
  method: "GET" | "POST";
  path: string;
  body?: unknown;
  authorization: string | null;
  conversationToken: string | null;
};

function conversationEngineMock(calls: Call[]): BackendClient {
  const record = (
    method: Call["method"],
    path: string,
    init?: RequestInit,
    body?: unknown
  ) => {
    const headers = new Headers(init?.headers);
    calls.push({
      method,
      path,
      body,
      authorization: headers.get("Authorization"),
      conversationToken: headers.get("X-VoxAgent-Conversation-Token")
    });
  };
  return {
    async fetchFromPython() {
      return new Response("{}", { status: 200 });
    },
    async parsePythonJson<T = unknown>(response: Response): Promise<T> {
      return response.json() as Promise<T>;
    },
    async getJsonFromPython<T = unknown>(path: string, init?: RequestInit): Promise<T> {
      record("GET", path, init);
      if (path.endsWith("/live")) {
        return {
          sessionId: "conv_123",
          host: liveDraft,
          guest: null,
          status: "ACTIVE"
        } as T;
      }
      return {
        sessionId: "conv_123",
        turns: [turn],
        nextSequence: 2,
        status: "ACTIVE"
      } as T;
    },
    async patchJsonToPython<T = unknown>(): Promise<T> {
      throw new Error("patch not used");
    },
    async postFormToPython<T = unknown>(): Promise<T> {
      throw new Error("form not used");
    },
    async postJsonToPython<T = unknown>(
      path: string,
      body: unknown,
      init?: RequestInit
    ): Promise<T> {
      record("POST", path, init, body);
      if (path === "/v1/conversation-sessions") {
        return {
          session,
          hostAccessToken: "host-token-with-enough-entropy-123",
          localGuestAccessToken: null,
          invite: {
            inviteToken: "invite-token-with-enough-entropy-123",
            qrPayload:
              "voxagent://conversation/join#v=1&token=invite-token-with-enough-entropy-123",
            expiresAt: "2026-07-19T12:05:00Z"
          }
        } as T;
      }
      if (path === "/public/v1/conversation-sessions/claim") {
        return {
          session: { ...session, status: "ACTIVE", participantCount: 2 },
          participantRole: "GUEST",
          accessToken: "guest-token-with-enough-entropy-123"
        } as T;
      }
      if (path.endsWith("/end")) {
        return { ...session, status: "ENDED" } as T;
      }
      if (path.endsWith("/live")) {
        return liveDraft as T;
      }
      return turn as T;
    }
  };
}

test("conversation contract is versioned, strict, and mode-consistent", () => {
  const created = conversationSessionCreatedSchema.parse({
    session,
    hostAccessToken: "host-token-with-enough-entropy-123",
    localGuestAccessToken: null,
    invite: {
      inviteToken: "invite-token-with-enough-entropy-123",
      qrPayload:
        "voxagent://conversation/join#v=1&token=invite-token-with-enough-entropy-123",
      expiresAt: "2026-07-19T12:05:00Z"
    }
  });
  assert.equal(created.session.schema, "voxagent.conversation.session");
  assert.equal(created.session.schemaVersion, 1);
  assert.equal(
    publishConversationTurnRequestSchema.safeParse({
      clientEventId: "hidden-field",
      speechEvent: { ...speechEvent, hiddenCredential: "reject-me" },
      translationEvent
    }).success,
    false
  );
  assert.equal(
    publishConversationLiveRequestSchema.safeParse({
      speechEvent: partialSpeechEvent
    }).success,
    true
  );
  assert.equal(conversationLiveDraftSchema.parse(liveDraft).revision, 1);
  assert.equal(
    conversationLiveSnapshotSchema.parse({
      sessionId: session.id,
      host: liveDraft,
      guest: null,
      status: "ACTIVE"
    }).host?.participantRole,
    "HOST"
  );
  assert.equal(
    conversationLiveDraftSchema.safeParse({
      ...liveDraft,
      revision: 2
    }).success,
    false
  );
  assert.equal(
    conversationLiveDraftSchema.safeParse({
      ...liveDraft,
      sessionId: "conv_other"
    }).success,
    false
  );
  assert.equal(
    conversationLiveDraftSchema.safeParse({
      ...liveDraft,
      targetLanguage: "en"
    }).success,
    false
  );
  assert.equal(
    publishConversationLiveRequestSchema.safeParse({
      speechEvent: {
        ...partialSpeechEvent,
        payload: { ...partialSpeechEvent.payload, revision: true }
      }
    }).success,
    false
  );
  assert.equal(
    publishConversationLiveRequestSchema.safeParse({
      speechEvent: {
        ...partialSpeechEvent,
        payload: { ...partialSpeechEvent.payload, text: "x".repeat(32_001) }
      }
    }).success,
    false
  );
  assert.equal(
    publishConversationLiveRequestSchema.safeParse({
      speechEvent: partialSpeechEvent,
      hiddenCredential: "reject-me"
    }).success,
    false
  );
  assert.equal(
    conversationLiveDraftSchema.safeParse({
      ...liveDraft,
      hiddenText: "reject-me"
    }).success,
    false
  );
});

test("gateway separates host identity from public conversation capability", async () => {
  const calls: Call[] = [];
  const app = await createApp(undefined, conversationEngineMock(calls));

  const created = await app.inject({
    method: "POST",
    url: "/v1/conversation-sessions",
    headers: { authorization: "Bearer real-host" },
    payload: {
      mode: "REMOTE_QR",
      hostLanguage: "en",
      guestLanguage: "zh-CN"
    }
  });
  const claimed = await app.inject({
    method: "POST",
    url: "/public/v1/conversation-sessions/claim",
    headers: { authorization: "Bearer must-not-forward" },
    payload: { inviteToken: created.json().invite.inviteToken }
  });

  assert.equal(created.statusCode, 201);
  assert.equal(claimed.statusCode, 200);
  assert.equal(calls[0]?.authorization, "Bearer real-host");
  assert.equal(calls[1]?.authorization, "");
  assert.equal(JSON.stringify(calls[1]?.body).includes("real-host"), false);
  await app.close();
});

test("gateway forwards conversation token only by header and encodes replay cursor", async () => {
  const calls: Call[] = [];
  const app = await createApp(undefined, conversationEngineMock(calls));
  const accessToken = "guest-token-with-enough-entropy-123";

  const replay = await app.inject({
    method: "GET",
    url: "/public/v1/conversation-sessions/conv%2Funsafe/turns?afterSequence=1",
    headers: { "x-voxagent-conversation-token": accessToken }
  });
  const published = await app.inject({
    method: "POST",
    url: "/public/v1/conversation-sessions/conv_123/turns",
    headers: { "x-voxagent-conversation-token": accessToken },
    payload: { clientEventId: "host-turn-1", speechEvent, translationEvent }
  });
  const invalidCursor = await app.inject({
    method: "GET",
    url: "/public/v1/conversation-sessions/conv_123/turns?afterSequence=true",
    headers: { "x-voxagent-conversation-token": accessToken }
  });

  assert.equal(replay.statusCode, 200);
  assert.equal(published.statusCode, 200);
  assert.equal(invalidCursor.statusCode, 400);
  assert.equal(calls[0]?.path, "/public/v1/conversation-sessions/conv%2Funsafe/turns?afterSequence=1");
  assert.equal(calls[0]?.conversationToken, accessToken);
  assert.equal(calls[0]?.authorization, "");
  assert.equal(calls[0]?.path.includes(accessToken), false);
  assert.equal(calls.length, 2);
  await app.close();
});

test("gateway validates live drafts and forwards only the participant header", async () => {
  const calls: Call[] = [];
  const app = await createApp(undefined, conversationEngineMock(calls));
  const accessToken = "guest-token-with-enough-entropy-123";

  const published = await app.inject({
    method: "POST",
    url: "/public/v1/conversation-sessions/conv_123/live",
    headers: {
      authorization: "Bearer must-not-forward",
      cookie: "must-not-forward=true",
      "x-voxagent-conversation-token": accessToken
    },
    payload: { speechEvent: partialSpeechEvent }
  });
  const snapshot = await app.inject({
    method: "GET",
    url: "/public/v1/conversation-sessions/conv_123/live",
    headers: {
      authorization: "Bearer must-not-forward",
      "x-voxagent-conversation-token": accessToken
    }
  });
  const hidden = await app.inject({
    method: "POST",
    url: "/public/v1/conversation-sessions/conv_123/live",
    headers: { "x-voxagent-conversation-token": accessToken },
    payload: {
      speechEvent: { ...partialSpeechEvent, hiddenCredential: "reject-me" }
    }
  });

  assert.equal(published.statusCode, 200);
  assert.equal(snapshot.statusCode, 200);
  assert.equal(hidden.statusCode, 400);
  assert.equal(published.json().type, "conversation.live");
  assert.equal(snapshot.json().host.sessionId, "conv_123");
  assert.equal(calls.length, 2);
  assert.equal(calls[0]?.path, "/public/v1/conversation-sessions/conv_123/live");
  assert.equal(calls[0]?.authorization, "");
  assert.equal(calls[0]?.conversationToken, accessToken);
  assert.equal(JSON.stringify(calls[0]?.body).includes(accessToken), false);
  assert.equal(calls[1]?.authorization, "");
  assert.equal(calls[1]?.conversationToken, accessToken);
  await app.close();
});

test("desktop advertises the conversation session capability", async () => {
  const app = await createApp(undefined, conversationEngineMock([]));
  const response = await app.inject({ method: "GET", url: "/v1/desktop/contract" });

  assert.equal(response.statusCode, 200);
  assert.equal(response.json().capabilities.includes("conversation.session.v1"), true);
  await app.close();
});

test("gateway preserves safe upstream conversation conflicts", async () => {
  const engine = conversationEngineMock([]);
  engine.postJsonToPython = async () => {
    const error = new Error("conversation replay conflict") as Error & {
      statusCode: number;
    };
    error.statusCode = 409;
    throw error;
  };
  const app = await createApp(undefined, engine);

  const response = await app.inject({
    method: "POST",
    url: "/public/v1/conversation-sessions/claim",
    payload: { inviteToken: "invite-token-with-enough-entropy-123" }
  });

  assert.equal(response.statusCode, 409);
  assert.equal(response.json().message, "conversation replay conflict");
  await app.close();
});
