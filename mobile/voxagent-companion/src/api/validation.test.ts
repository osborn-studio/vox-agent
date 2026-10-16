import { describe, expect, it } from "vitest";

import {
  isAgentAnswer,
  isCaptureSession,
  isConversationLiveSnapshot,
  isConversationTurn,
  isMemorySearchResponse,
  isPairingCode,
  isSpeechAudioChunkAck,
  isSpeechStreamCreated,
  isSpeechTerminalEvent,
  isTokenBalance,
  isTokenLedger,
} from "./validation";

const captureSession = {
  id: "cap_contract",
  workspaceId: "ws_contract",
  ownerUserId: "user_contract",
  edition: "PERSONAL",
  source: "MOBILE",
  mode: "FIELD_NOTE",
  title: null,
  startedAt: "2026-07-26T12:00:00Z",
  endedAt: null,
  languageHints: ["en"],
  detectedLanguages: [],
  privacyLevel: "PERSONAL",
  consentState: "GRANTED",
  deviceIds: [],
  localOnlyUntilSync: false,
  status: "RECORDING",
  createdAt: "2026-07-26T12:00:00Z",
  updatedAt: "2026-07-26T12:00:00Z",
};

const memoryNode = {
  id: "mem_contract",
  workspaceId: "ws_contract",
  ownerUserId: null,
  sourceSessionId: "cap_contract",
  type: "PROMISE",
  title: "Send the release brief",
  body: "Send it by Friday.",
  language: "en",
  importance: "HIGH",
  visibility: "PRIVATE",
  tags: ["release"],
  metadata: { status: "open" },
  confidence: 0.9,
  createdAt: "2026-07-26T12:00:00Z",
  updatedAt: "2026-07-26T12:00:00Z",
};

const speechEvent = {
  schema: "voxagent.speech.event",
  schemaVersion: 1,
  eventId: "speech-event-1",
  type: "transcript.final",
  streamId: "speech-stream-1",
  sessionId: "conv_contract",
  sequence: 1,
  monotonicMs: 500,
  emittedAt: "2026-07-26T12:00:01Z",
  payload: {
    segmentId: "segment-1",
    revision: 1,
    text: "Hello",
    language: { code: "en", confidence: 0.98 },
    speaker: { id: "speaker-host", label: null },
    audioStartMs: 0,
    audioEndMs: 500,
    confidence: 0.97,
  },
};

const translationEvent = {
  schema: "voxagent.translation.event",
  schemaVersion: 1,
  eventId: "translation-event-1",
  translationId: "translation-1",
  type: "translation.final",
  emittedAt: "2026-07-26T12:00:02Z",
  latencyMs: 50,
  payload: {
    sourceLanguage: "en",
    targetLanguage: "zh-CN",
    text: "你好",
    confidence: 0.95,
    provider: "local",
    model: "translation-model",
    sourceSegments: [
      {
        streamId: "speech-stream-1",
        segmentId: "segment-1",
        revision: 1,
        audioStartMs: 0,
        audioEndMs: 500,
      },
    ],
    glossaryHits: [],
  },
};

const conversationTurn = {
  schema: "voxagent.conversation.turn",
  schemaVersion: 1,
  type: "conversation.turn",
  eventId: "turn-event-1",
  sessionId: "conv_contract",
  sequence: 1,
  participantRole: "HOST",
  clientEventId: "client-event-1",
  speechEvent,
  translationEvent,
  createdAt: "2026-07-26T12:00:03Z",
};

const conversationLiveSnapshot = {
  sessionId: "conv_contract",
  host: {
    schema: "voxagent.conversation.live",
    schemaVersion: 1,
    type: "conversation.live",
    sessionId: "conv_contract",
    participantRole: "HOST",
    streamId: "speech-stream-1",
    segmentId: "segment-1",
    revision: 2,
    sourceLanguage: "en",
    targetLanguage: "zh-CN",
    speechEvent: {
      ...speechEvent,
      eventId: "speech-partial-2",
      type: "transcript.partial",
      sequence: 2,
      payload: {
        ...speechEvent.payload,
        revision: 2,
        text: "Hello there",
      },
    },
    updatedAt: "2026-07-26T12:00:02Z",
  },
  guest: null,
  status: "ACTIVE",
};

describe("speech transport validators", () => {
  it("accepts strict stream, chunk, and terminal envelopes", () => {
    expect(
      isSpeechStreamCreated({
        streamId: "speech-mobile-1",
        resumeToken: "resume-token-with-enough-entropy",
        schema: "voxagent.speech.event",
        schemaVersion: 1,
        nextSequence: 2,
      })
    ).toBe(true);
    expect(
      isSpeechAudioChunkAck({
        schema: "voxagent.speech.audio-chunk",
        schemaVersion: 1,
        streamId: "speech-mobile-1",
        chunkIndex: 0,
        acceptedBytes: 4,
        totalBytes: 4,
        durationMs: 0,
        nextChunkIndex: 1,
      })
    ).toBe(true);
    expect(
      isSpeechTerminalEvent({
        schema: "voxagent.speech.event",
        schemaVersion: 1,
        eventId: "terminal-1",
        streamId: "speech-mobile-1",
        sessionId: "conversation-1",
        sequence: 3,
        type: "stream.completed",
        monotonicMs: 1000,
        emittedAt: "2026-07-26T12:00:00Z",
        payload: { reason: "input_finished" },
      })
    ).toBe(true);
  });

  it("rejects drifted or logically inconsistent chunk acknowledgements", () => {
    const ack = {
      schema: "voxagent.speech.audio-chunk",
      schemaVersion: 1,
      streamId: "speech-mobile-1",
      chunkIndex: 0,
      acceptedBytes: 4,
      totalBytes: 4,
      durationMs: 0,
      nextChunkIndex: 1,
    };
    expect(isSpeechAudioChunkAck({ ...ack, nextChunkIndex: 2 })).toBe(false);
    expect(isSpeechAudioChunkAck({ ...ack, rawAudio: "forbidden" })).toBe(false);
  });
});

describe("canonical mobile response validators", () => {
  it("accepts current capture fields and rejects the former reduced shape", () => {
    expect(isCaptureSession(captureSession)).toBe(true);
    const { edition: _edition, ...legacyShape } = captureSession;
    expect(isCaptureSession(legacyShape)).toBe(false);
  });

  it("accepts canonical memory search metadata and node types", () => {
    expect(
      isMemorySearchResponse({
        query: "release",
        results: [{ node: memoryNode, score: 1.2, highlights: ["Friday"] }],
      })
    ).toBe(true);
    expect(
      isMemorySearchResponse({
        query: "release",
        results: [
          {
            node: { ...memoryNode, type: "NOTE" },
            score: 1.2,
            highlights: [],
          },
        ],
      })
    ).toBe(false);
  });

  it("validates pairing and agent result contracts", () => {
    expect(
      isPairingCode({
        code: "A1B2C3",
        qrPayload: "voxagent://pair/A1B2C3",
        expiresAt: "2026-07-26T12:05:00Z",
      })
    ).toBe(true);
    expect(
      isAgentAnswer({
        query: "What did I promise?",
        answer: "Send the brief.",
        answerer: "local",
        citations: [{ id: "mem_contract", title: null, type: "PROMISE" }],
        contextNodeIds: ["mem_contract"],
      })
    ).toBe(true);
  });

  it("requires complete integer token balance and ledger responses", () => {
    expect(
      isTokenBalance({
        scopeId: "user_local",
        scopeType: "USER",
        balance: 100_000,
        currency: "TOKENS",
        updatedAt: "2026-07-26T12:00:00Z",
      })
    ).toBe(true);
    expect(isTokenBalance({ balance: 100_000, currency: "TOKENS" })).toBe(
      false
    );
    expect(
      isTokenLedger([
        {
          id: "ledger_1",
          scopeId: "user_local",
          scopeType: "USER",
          sessionId: null,
          spendType: "PURCHASE",
          delta: 100_000,
          balanceAfter: 100_000,
          description: "initial grant",
          createdAt: "2026-07-26T12:00:00Z",
        },
      ])
    ).toBe(true);
  });

  it("accepts a cited conversation turn and rejects citation drift", () => {
    expect(isConversationTurn(conversationTurn)).toBe(true);
    expect(
      isConversationTurn({
        ...conversationTurn,
        translationEvent: {
          ...translationEvent,
          payload: {
            ...translationEvent.payload,
            sourceSegments: [
              {
                ...translationEvent.payload.sourceSegments[0],
                segmentId: "different-segment",
              },
            ],
          },
        },
      })
    ).toBe(false);
  });

  it("accepts a strict live snapshot and rejects revision or metadata drift", () => {
    expect(isConversationLiveSnapshot(conversationLiveSnapshot)).toBe(true);
    expect(
      isConversationLiveSnapshot({
        ...conversationLiveSnapshot,
        host: {
          ...conversationLiveSnapshot.host,
          revision: 1,
        },
      })
    ).toBe(false);
    expect(
      isConversationLiveSnapshot({
        ...conversationLiveSnapshot,
        unexpected: "must fail closed",
      })
    ).toBe(false);
  });
});
