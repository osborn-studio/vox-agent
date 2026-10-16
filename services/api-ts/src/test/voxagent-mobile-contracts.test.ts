import assert from "node:assert/strict";
import test from "node:test";

import {
  captureSessionSchema,
  transcriptSegmentSchema,
  memoryNodeSchema,
  memoryEdgeSchema,
  memorySearchResponseSchema,
  deviceSchema,
  devicePairingSchema,
  deviceEventSchema,
  deviceToolInvocationSchema,
  tokenBalanceSchema,
  agentAnswerSchema,
  agentRecentContextSchema,
  auditEventListSchema,
  modelPolicySchema,
  retentionPolicySchema,
  speechStreamCreatedSchema,
  speechEventSchema,
  speechReplaySchema,
  translationRequestSchema,
  translationEventSchema
} from "@voxlog/contracts";

import type { BackendClient } from "../lib/backend-client.js";
import { createApp } from "../app.js";

test("parses versioned multilingual speech stream events and replay", () => {
  const created = speechStreamCreatedSchema.parse({
    streamId: "speech_123",
    resumeToken: "resume-token-with-enough-entropy",
    schema: "voxagent.speech.event",
    schemaVersion: 1,
    nextSequence: 2
  });
  const event = speechEventSchema.parse({
    schema: "voxagent.speech.event",
    schemaVersion: 1,
    eventId: "speech_event_1",
    streamId: created.streamId,
    sessionId: "session-1",
    sequence: 2,
    type: "transcript.final",
    monotonicMs: 800,
    emittedAt: "2026-07-18T12:00:00Z",
    payload: {
      segmentId: "segment-1",
      revision: 1,
      text: "こんにちは",
      language: { code: "ja", confidence: 0.99 },
      speaker: { id: "speaker-a", label: "A" },
      audioStartMs: 0,
      audioEndMs: 800,
      confidence: 0.96
    }
  });
  const replay = speechReplaySchema.parse({
    streamId: created.streamId,
    events: [event],
    nextSequence: 3,
    terminal: false
  });

  assert.equal(event.type, "transcript.final");
  if (event.type !== "transcript.final") assert.fail("expected a final transcript event");
  assert.equal(event.payload.language.code, "ja");
  assert.equal(replay.events[0].sequence, 2);
});

test("rejects speech events without monotonic time or speaker identity", () => {
  assert.throws(() => speechEventSchema.parse({
    schema: "voxagent.speech.event",
    schemaVersion: 1,
    eventId: "speech_event_1",
    streamId: "speech_123",
    sequence: 2,
    type: "transcript.partial",
    emittedAt: "2026-07-18T12:00:00Z",
    payload: {
      segmentId: "segment-1",
      revision: 1,
      text: "hello",
      language: { code: "en", confidence: 0.9 },
      audioStartMs: 0,
      audioEndMs: 400
    }
  }));
});

test("parses cited realtime translation requests and final events", () => {
  const request = translationRequestSchema.parse({
    sourceLanguage: "ja",
    targetLanguage: "en",
    segments: [
      {
        streamId: "speech_123",
        segmentId: "segment-1",
        revision: 2
      }
    ],
    glossary: [{ sourceText: "会議", targetText: "meeting" }],
    allowRemote: true,
    idempotencyKey: "translation-request-1"
  });
  const event = translationEventSchema.parse({
    schema: "voxagent.translation.event",
    schemaVersion: 1,
    eventId: "translation_event_1",
    translationId: "translation_1",
    type: "translation.final",
    emittedAt: "2026-07-18T12:00:00Z",
    latencyMs: 83,
    payload: {
      sourceLanguage: request.sourceLanguage,
      targetLanguage: request.targetLanguage,
      text: "Tomorrow's meeting is at ten.",
      confidence: 0.94,
      provider: "openai-compatible",
      model: "translation-model",
      sourceSegments: [
        {
          streamId: "speech_123",
          segmentId: "segment-1",
          revision: 2,
          audioStartMs: 1_200,
          audioEndMs: 2_400
        }
      ],
      glossaryHits: [{ sourceText: "会議", targetText: "meeting" }]
    }
  });

  assert.equal(event.type, "translation.final");
  if (event.type !== "translation.final") assert.fail("expected a final translation event");
  assert.equal(event.payload.sourceSegments[0].segmentId, "segment-1");
  assert.equal(event.payload.glossaryHits[0].targetText, "meeting");
});

test("translation failure events disclose no source transcript text", () => {
  const event = translationEventSchema.parse({
    schema: "voxagent.translation.event",
    schemaVersion: 1,
    eventId: "translation_event_2",
    translationId: "translation_2",
    type: "translation.failed",
    emittedAt: "2026-07-18T12:00:00Z",
    latencyMs: 0,
    payload: {
      sourceLanguage: "zh-Hans",
      targetLanguage: "en",
      code: "REMOTE_DISABLED",
      message: "Remote translation requires explicit consent.",
      retriable: false,
      sourceSegments: [
        {
          streamId: "speech_123",
          segmentId: "segment-private",
          revision: 1,
          audioStartMs: 0,
          audioEndMs: 900
        }
      ]
    }
  });

  assert.equal(event.type, "translation.failed");
  assert.equal("text" in event.payload, false);
  assert.throws(() => translationEventSchema.parse({
    ...event,
    payload: { ...event.payload, sourceText: "must not cross the failure boundary" }
  }));
});

test("parses a valid capture session", () => {
  const parsed = captureSessionSchema.parse({
    id: "cap_123",
    workspaceId: "ws_123",
    ownerUserId: "user_123",
    source: "MOBILE",
    mode: "MEETING",
    startedAt: "2026-07-04T08:00:00+09:00",
    endedAt: null,
    languageHints: ["zh", "ja", "en"],
    privacyLevel: "PERSONAL",
    consentState: "UNKNOWN",
    deviceIds: ["dev_123"],
    localOnlyUntilSync: true,
    status: "RECORDING"
  });

  assert.equal(parsed.id, "cap_123");
  assert.equal(parsed.status, "RECORDING");
});

test("rejects unknown capture session status", () => {
  assert.throws(() => {
    captureSessionSchema.parse({
      id: "cap_123",
      workspaceId: "ws_123",
      ownerUserId: "user_123",
      source: "MOBILE",
      mode: "MEETING",
      startedAt: "2026-07-04T08:00:00+09:00",
      endedAt: null,
      languageHints: [],
      privacyLevel: "PERSONAL",
      consentState: "UNKNOWN",
      deviceIds: [],
      localOnlyUntilSync: false,
      status: "DONE"
    });
  });
});

test("parses a transcript segment", () => {
  const parsed = transcriptSegmentSchema.parse({
    id: "seg_123",
    sessionId: "cap_123",
    speakerId: "speaker_1",
    startMs: 0,
    endMs: 2400,
    textRaw: "hello",
    textNormalized: "Hello.",
    textTranslated: "你好。",
    confidence: 0.92,
    model: "mock-asr",
    isUserMarked: false,
    embeddingId: null
  });

  assert.equal(parsed.endMs, 2400);
});

test("parses a memory node", () => {
  const parsed = memoryNodeSchema.parse({
    id: "mem_123",
    workspaceId: "ws_123",
    sourceSessionId: "cap_123",
    type: "DECISION",
    title: "Launch mobile MVP",
    body: "The team decided to build Capture, Memory, and Agents first.",
    tags: ["mobile", "mvp"],
    confidence: 0.88,
    createdAt: "2026-07-04T08:30:00+09:00",
    updatedAt: "2026-07-04T08:30:00+09:00"
  });

  assert.equal(parsed.type, "DECISION");
});

test("parses memory search response", () => {
  const parsed = memorySearchResponseSchema.parse({
    query: "mobile MVP",
    results: [
      {
        node: {
          id: "mem_123",
          workspaceId: "ws_123",
          sourceSessionId: "cap_123",
          type: "DECISION",
          title: "Launch mobile MVP",
          body: "Build Capture, Memory, and Agents first.",
          tags: ["mobile"],
          confidence: 0.9,
          createdAt: "2026-07-04T08:30:00+09:00",
          updatedAt: "2026-07-04T08:30:00+09:00"
        },
        score: 0.77,
        highlights: ["Build Capture, Memory, and Agents first."]
      }
    ]
  });

  assert.equal(parsed.results.length, 1);
});

test("parses a memory edge", () => {
  const parsed = memoryEdgeSchema.parse({
    id: "edge_123",
    fromNodeId: "mem_123",
    toNodeId: "mem_456",
    relation: "RELATES_TO",
    confidence: 0.5,
    createdAt: "2026-07-04T08:30:00+09:00"
  });

  assert.equal(parsed.relation, "RELATES_TO");
});

test("parses device contracts", () => {
  const device = deviceSchema.parse({
    id: "dev_123",
    workspaceId: "ws_123",
    ownerUserId: "user_123",
    kind: "SIMULATOR",
    displayName: "Simulated VOXPIN",
    status: "CONNECTED",
    batteryPercent: 84,
    firmwareVersion: "0.1.0",
    pairedAt: "2026-07-04T08:00:00+09:00",
    lastSeenAt: "2026-07-04T08:20:00+09:00"
  });
  assert.equal(device.kind, "SIMULATOR");

  const event = deviceEventSchema.parse({
    id: "evt_123",
    deviceId: "dev_123",
    type: "RECORD_BUTTON_PRESSED",
    occurredAt: "2026-07-04T08:20:00+09:00",
    payload: { batteryPercent: 84 },
    signature: null
  });
  assert.equal(event.type, "RECORD_BUTTON_PRESSED");

  const invocation = deviceToolInvocationSchema.parse({
    id: "tool_123",
    deviceId: "dev_123",
    tool: "DEVICE_VIBRATE",
    args: { seconds: 1 },
    requestedAt: "2026-07-04T08:21:00+09:00",
    status: "REQUESTED"
  });
  assert.equal(invocation.tool, "DEVICE_VIBRATE");
});

test("parses a device pairing", () => {
  const parsed = devicePairingSchema.parse({
    id: "pair_123",
    code: "123456",
    qrPayload: "voxagent://pair/123456",
    workspaceId: "ws_123",
    ownerUserId: "user_123",
    expiresAt: "2026-07-04T09:00:00+09:00",
    claimedAt: null,
    status: "PENDING"
  });

  assert.equal(parsed.status, "PENDING");
});

test("parses token balance", () => {
  const parsed = tokenBalanceSchema.parse({
    scopeId: "user_123",
    scopeType: "USER",
    balance: 12000,
    currency: "TOKENS",
    updatedAt: "2026-07-04T08:00:00+09:00"
  });

  assert.equal(parsed.balance, 12000);
});

test("parses an agent answer", () => {
  const parsed = agentAnswerSchema.parse({
    query: "japan launch",
    answer: "Based on 1 memory item...",
    answerer: "mock",
    citations: [{ id: "mem_1", title: "Launch in Japan", type: "DECISION" }],
    contextNodeIds: ["mem_1"]
  });
  assert.equal(parsed.citations[0].id, "mem_1");
});

test("parses recent agent context", () => {
  const parsed = agentRecentContextSchema.parse({
    days: 7,
    count: 1,
    nodes: [{ id: "mem_1", title: "T", type: "DECISION", createdAt: "t" }]
  });
  assert.equal(parsed.nodes.length, 1);
});

test("parses an audit event list", () => {
  const parsed = auditEventListSchema.parse([
    { id: "aud_1", action: "agent.query", actorId: null, workspaceId: "ws",
      targetId: null, metadata: { query: "x" }, createdAt: "t" }
  ]);
  assert.equal(parsed[0].action, "agent.query");
});

function createMobileMockClient(): BackendClient {
  return {
    async fetchFromPython(): Promise<Response> {
      throw new Error("fetchFromPython not used in this test");
    },
    async getJsonFromPython<T = unknown>(path: string): Promise<T> {
      if (path.startsWith("/v1/admin/audit-events")) {
        return [
          {
            id: "aud_1",
            action: "agent.query",
            actorId: null,
            workspaceId: "ws_123",
            targetId: null,
            metadata: { query: "mobile" },
            createdAt: "2026-07-05T08:00:00+09:00"
          }
        ] as T;
      }
      if (path === "/v1/admin/model-policy") {
        return {
          answerer: "mock",
          asrAdapter: "mock",
          answererOverride: null,
          asrAdapterOverride: null,
          availableAnswerers: ["llm", "mock"],
          availableAsrAdapters: ["mock", "sherpa"],
          llmConfigured: false,
          llmModel: null
        } as T;
      }
      if (path === "/v1/admin/retention-policy") {
        return {
          audioRetentionDays: 30,
          transcriptRetentionDays: 90,
          memoryRetentionDays: 365,
          autoDeleteEnabled: false,
          audioRetentionDaysOverride: null,
          transcriptRetentionDaysOverride: null,
          memoryRetentionDaysOverride: null,
          autoDeleteOverride: null
        } as T;
      }
      if (path === "/v1/admin/users") {
        return [{
          id: "user_admin",
          orgId: "org_1",
          email: "admin@example.com",
          displayName: "Administrator",
          role: "admin",
          status: "active",
          createdAt: "2026-07-17T00:00:00Z"
        }] as T;
      }
      if (path.startsWith("/v1/memory/search")) {
        return {
          query: "mobile",
          results: []
        } as T;
      }
      if (path.startsWith("/v1/memory/recent")) {
        return [] as T;
      }
      if (path.startsWith("/v1/memory/action-items")) {
        return [] as T;
      }
      if (path.startsWith("/v1/memory/nodes/")) {
        return {
          id: "mem_123",
          workspaceId: "ws_123",
          ownerUserId: null,
          sourceSessionId: "cap_123",
          type: "DECISION",
          title: "Launch mobile MVP",
          body: "Build Capture, Memory, and Agents first.",
          language: null,
          importance: "MEDIUM",
          visibility: "PRIVATE",
          tags: ["mobile"],
          metadata: {},
          confidence: 0.9,
          createdAt: "2026-07-04T08:30:00+09:00",
          updatedAt: "2026-07-04T08:30:00+09:00"
        } as T;
      }
      if (path.startsWith("/v1/capture/sessions/") && path.endsWith("/transcript-segments")) {
        return [] as T;
      }
      if (path.startsWith("/v1/capture/sessions?")) {
        return [] as T;
      }
      if (path.startsWith("/v1/capture/sessions/") && path.endsWith("/summary")) {
        return {
          sessionId: "cap_123",
          status: "READY",
          summaryNodes: [
            {
              id: "mem_sum",
              workspaceId: "ws_123",
              sourceSessionId: "cap_123",
              type: "SUMMARY",
              title: "Summary: cap_123",
              body: "Deterministic summary.",
              tags: ["auto-summary"],
              confidence: 0.9,
              createdAt: "2026-07-04T08:30:00+09:00",
              updatedAt: "2026-07-04T08:30:00+09:00"
            }
          ],
          actionItems: [],
          decisions: []
        } as T;
      }
      if (path.startsWith("/v1/capture/sessions/")) {
        return {
          id: "cap_123",
          workspaceId: "ws_123",
          ownerUserId: "user_123",
          source: "MOBILE",
          mode: "MEETING",
          startedAt: "2026-07-04T08:00:00+09:00",
          endedAt: null,
          languageHints: ["en"],
          privacyLevel: "PERSONAL",
          consentState: "UNKNOWN",
          deviceIds: ["dev_123"],
          localOnlyUntilSync: true,
          status: "RECORDING"
        } as T;
      }
      if (path === "/v1/devices") {
        return [] as T;
      }
      if (path === "/v1/tokens/balance") {
        return {
          scopeId: "user_123",
          scopeType: "USER",
          balance: 100,
          currency: "TOKENS",
          updatedAt: "2026-07-04T08:00:00+09:00"
        } as T;
      }
      if (path === "/v1/tokens/ledger") {
        return [] as T;
      }
      if (path.startsWith("/v1/agents/context/recent")) {
        return {
          days: 7,
          count: 1,
          nodes: [
            {
              id: "mem_recent",
              title: "Recent decision",
              type: "DECISION",
              createdAt: "2026-07-04T08:30:00+09:00"
            }
          ]
        } as T;
      }
      return {} as T;
    },
    async parsePythonJson<T = unknown>(): Promise<T> {
      throw new Error("parsePythonJson not used in this test");
    },
    async patchJsonToPython<T = unknown>(path: string, body: unknown): Promise<T> {
      if (path === "/v1/admin/model-policy") {
        const payload = body as Record<string, unknown>;
        const answerer = (payload.answerer as string | null | undefined) ?? null;
        return {
          answerer: answerer ?? "mock",
          asrAdapter: "mock",
          answererOverride: answerer,
          asrAdapterOverride: null,
          availableAnswerers: ["llm", "mock"],
          availableAsrAdapters: ["mock", "sherpa"],
          llmConfigured: false,
          llmModel: null
        } as T;
      }
      if (path === "/v1/admin/retention-policy") {
        const payload = body as Record<string, unknown>;
        const audioOverride = (payload.audioRetentionDays as number | null | undefined) ?? null;
        return {
          audioRetentionDays: audioOverride ?? 30,
          transcriptRetentionDays: 90,
          memoryRetentionDays: 365,
          autoDeleteEnabled: false,
          audioRetentionDaysOverride: audioOverride,
          transcriptRetentionDaysOverride: null,
          memoryRetentionDaysOverride: null,
          autoDeleteOverride: null
        } as T;
      }
      if (path === "/v1/capture/sessions/cap_123") {
        const payload = body as Record<string, unknown>;
        return {
          id: "cap_123",
          workspaceId: "ws_123",
          ownerUserId: "user_123",
          source: "MOBILE",
          mode: "MEETING",
          startedAt: "2026-07-04T08:00:00+09:00",
          endedAt: "2026-07-04T09:00:00+09:00",
          languageHints: ["en"],
          privacyLevel: "PERSONAL",
          consentState: "UNKNOWN",
          deviceIds: ["dev_123"],
          localOnlyUntilSync: true,
          status: (payload.status as string) ?? "READY"
        } as T;
      }
      return {} as T;
    },
    async postFormToPython<T = unknown>(): Promise<T> {
      throw new Error("postFormToPython not used in this test");
    },
    async postJsonToPython<T = unknown>(path: string, body: unknown): Promise<T> {
      if (path === "/v1/capture/sessions") {
        const payload = body as Record<string, unknown>;
        return {
          id: "cap_123",
          workspaceId: payload.workspaceId ?? "ws_123",
          ownerUserId: payload.ownerUserId ?? "user_123",
          source: payload.source ?? "MOBILE",
          mode: payload.mode ?? "MEETING",
          startedAt: payload.startedAt ?? "2026-07-04T08:00:00+09:00",
          endedAt: null,
          languageHints: payload.languageHints ?? [],
          privacyLevel: payload.privacyLevel ?? "PERSONAL",
          consentState: payload.consentState ?? "UNKNOWN",
          deviceIds: payload.deviceIds ?? [],
          localOnlyUntilSync: payload.localOnlyUntilSync ?? true,
          status: "RECORDING"
        } as T;
      }
      if (path.endsWith("/process")) {
        return {
          id: "cap_123",
          workspaceId: "ws_123",
          ownerUserId: "user_123",
          source: "MOBILE",
          mode: "MEETING",
          startedAt: "2026-07-04T08:00:00+09:00",
          endedAt: "2026-07-04T09:00:00+09:00",
          languageHints: ["zh", "ja", "en"],
          detectedLanguages: ["zh", "ja", "en"],
          privacyLevel: "PERSONAL",
          consentState: "UNKNOWN",
          deviceIds: ["dev_123"],
          localOnlyUntilSync: true,
          status: "READY"
        } as T;
      }
      if (path.endsWith("/transcript-segments")) {
        return {
          id: "seg_123",
          sessionId: "cap_123",
          speakerId: "speaker_1",
          speakerLabel: null,
          startMs: 0,
          endMs: 2400,
          language: null,
          textRaw: "hello",
          textNormalized: "Hello.",
          textTranslated: null,
          translationTargetLanguage: null,
          confidence: 0.9,
          model: "mock-asr",
          isUserMarked: false,
          embeddingId: null
        } as T;
      }
      if (path === "/v1/memory/nodes") {
        return {
          id: "mem_123",
          workspaceId: "ws_123",
          ownerUserId: null,
          sourceSessionId: null,
          type: "DECISION",
          title: "Launch mobile MVP",
          body: "Build Capture, Memory, and Agents first.",
          language: null,
          importance: "MEDIUM",
          visibility: "PRIVATE",
          tags: [],
          metadata: {},
          confidence: 0.9,
          createdAt: "2026-07-04T08:30:00+09:00",
          updatedAt: "2026-07-04T08:30:00+09:00"
        } as T;
      }
      if (path === "/v1/devices/pairing-codes") {
        return {
          code: "123456",
          qrPayload: "voxagent://pair/123456",
          expiresAt: "2026-07-04T09:00:00+09:00"
        } as T;
      }
      if (path === "/v1/devices/pair") {
        return {
          id: "dev_123",
          workspaceId: "ws_123",
          ownerUserId: "user_123",
          kind: "SIMULATOR",
          displayName: "Simulated VOXPIN",
          status: "CONNECTED",
          batteryPercent: 84,
          firmwareVersion: "0.1.0",
          pairedAt: "2026-07-04T08:00:00+09:00",
          lastSeenAt: "2026-07-04T08:20:00+09:00"
        } as T;
      }
      if (path.endsWith("/events")) {
        return {
          id: "evt_123",
          deviceId: "dev_123",
          type: "RECORD_BUTTON_PRESSED",
          occurredAt: "2026-07-04T08:20:00+09:00",
          payload: { batteryPercent: 84 },
          signature: null,
          sessionId: null
        } as T;
      }
      if (path.endsWith("/tools")) {
        return {
          id: "tool_123",
          deviceId: "dev_123",
          tool: "DEVICE_VIBRATE",
          args: { seconds: 1 },
          requestedAt: "2026-07-04T08:21:00+09:00",
          status: "REQUESTED"
        } as T;
      }
      if (path === "/v1/agents/query") {
        const payload = body as Record<string, unknown>;
        const query = (payload.query as string) ?? "";
        return {
          query,
          answer: `Based on 1 memory item(s) for "${query}": Launch in Japan`,
          answerer: "mock",
          citations: [{ id: "mem_1", title: "Launch in Japan", type: "DECISION" }],
          contextNodeIds: ["mem_1"]
        } as T;
      }
      return {} as T;
    }
  };
}

test("POST /v1/capture/sessions creates a capture session", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "POST",
    url: "/v1/capture/sessions",
    headers: { "content-type": "application/json" },
    payload: JSON.stringify({
      workspaceId: "ws_123",
      ownerUserId: "user_123",
      source: "MOBILE",
      mode: "MEETING",
      startedAt: "2026-07-04T08:00:00+09:00",
      languageHints: ["en"],
      privacyLevel: "PERSONAL",
      consentState: "UNKNOWN",
      deviceIds: ["dev_123"],
      localOnlyUntilSync: true
    })
  });

  assert.equal(response.statusCode, 200);
  assert.equal(response.json().id, "cap_123");
  assert.equal(response.json().status, "RECORDING");

  await app.close();
});

test("capture routes forward the caller credential without inheriting service identity", async () => {
  const backend = createMobileMockClient();
  const postJson = backend.postJsonToPython.bind(backend);
  let forwardedAuthorization: string | null = null;
  backend.postJsonToPython = async <T = unknown>(
    path: string,
    body: unknown,
    init?: RequestInit
  ): Promise<T> => {
    forwardedAuthorization = new Headers(init?.headers).get("Authorization");
    return postJson<T>(path, body, init);
  };
  const app = await createApp(backend);

  const response = await app.inject({
    method: "POST",
    url: "/v1/capture/sessions",
    headers: {
      "content-type": "application/json",
      authorization: "Bearer member-capture-token"
    },
    payload: JSON.stringify({
      source: "MOBILE",
      mode: "MEETING",
      languageHints: ["en"],
      privacyLevel: "PERSONAL",
      consentState: "UNKNOWN",
      deviceIds: [],
      localOnlyUntilSync: false
    })
  });

  assert.equal(response.statusCode, 200);
  assert.equal(forwardedAuthorization, "Bearer member-capture-token");
  await app.close();
});

test("PATCH /v1/capture/sessions/:id updates a capture session", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "PATCH",
    url: "/v1/capture/sessions/cap_123",
    headers: { "content-type": "application/json" },
    payload: JSON.stringify({ status: "READY" })
  });

  assert.equal(response.statusCode, 200);
  assert.equal(response.json().id, "cap_123");
  assert.equal(response.json().status, "READY");

  await app.close();
});

test("GET /v1/memory/search returns validated search response", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "GET",
    url: "/v1/memory/search?q=mobile"
  });

  assert.equal(response.statusCode, 200);
  assert.equal(response.json().query, "mobile");
  assert.deepEqual(response.json().results, []);

  await app.close();
});

test("memory and Agent routes forward the caller principal credential", async () => {
  const backend = createMobileMockClient();
  const getJson = backend.getJsonFromPython.bind(backend);
  const postJson = backend.postJsonToPython.bind(backend);
  const forwarded: Array<{ path: string; authorization: string | null }> = [];
  backend.getJsonFromPython = async <T = unknown>(
    path: string,
    init?: RequestInit
  ): Promise<T> => {
    forwarded.push({
      path,
      authorization: new Headers(init?.headers).get("Authorization")
    });
    return getJson<T>(path, init);
  };
  backend.postJsonToPython = async <T = unknown>(
    path: string,
    body: unknown,
    init?: RequestInit
  ): Promise<T> => {
    forwarded.push({
      path,
      authorization: new Headers(init?.headers).get("Authorization")
    });
    return postJson<T>(path, body, init);
  };
  const app = await createApp(backend);

  await app.inject({
    method: "GET",
    url: "/v1/memory/search?q=mobile",
    headers: { Authorization: "Bearer caller-memory-token" }
  });
  await app.inject({
    method: "POST",
    url: "/v1/agents/query",
    headers: {
      Authorization: "Bearer caller-memory-token",
      "content-type": "application/json"
    },
    payload: JSON.stringify({ query: "x" })
  });

  assert.deepEqual(forwarded, [
    {
      path: "/v1/memory/search?q=mobile&limit=20",
      authorization: "Bearer caller-memory-token"
    },
    {
      path: "/v1/agents/query",
      authorization: "Bearer caller-memory-token"
    }
  ]);
  await app.close();
});

test("Mobile list and action-item reads exist and forward caller identity", async () => {
  const backend = createMobileMockClient();
  const getJson = backend.getJsonFromPython.bind(backend);
  const forwarded: Array<{ path: string; authorization: string | null }> = [];
  backend.getJsonFromPython = async <T = unknown>(
    path: string,
    init?: RequestInit
  ): Promise<T> => {
    forwarded.push({
      path,
      authorization: new Headers(init?.headers).get("Authorization")
    });
    return getJson<T>(path, init);
  };
  const app = await createApp(backend);

  const sessions = await app.inject({
    method: "GET",
    url: "/v1/capture/sessions?limit=25",
    headers: { Authorization: "Bearer caller-mobile-token" }
  });
  const actionItems = await app.inject({
    method: "GET",
    url: "/v1/memory/action-items?status=OPEN",
    headers: { Authorization: "Bearer caller-mobile-token" }
  });

  assert.equal(sessions.statusCode, 200);
  assert.deepEqual(sessions.json(), []);
  assert.equal(actionItems.statusCode, 200);
  assert.deepEqual(actionItems.json(), []);
  assert.deepEqual(forwarded, [
    {
      path: "/v1/capture/sessions?limit=25",
      authorization: "Bearer caller-mobile-token"
    },
    {
      path: "/v1/memory/action-items?status=OPEN",
      authorization: "Bearer caller-mobile-token"
    }
  ]);
  await app.close();
});

test("GET /v1/devices returns validated device list", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "GET",
    url: "/v1/devices"
  });

  assert.equal(response.statusCode, 200);
  assert.deepEqual(response.json(), []);

  await app.close();
});

test("GET /v1/tokens/balance returns validated balance", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "GET",
    url: "/v1/tokens/balance"
  });

  assert.equal(response.statusCode, 200);
  assert.equal(response.json().balance, 100);

  await app.close();
});

test("POST /v1/capture/sessions/:id/process returns processed session", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "POST",
    url: "/v1/capture/sessions/cap_123/process",
    headers: { "content-type": "application/json" },
    payload: JSON.stringify({})
  });

  assert.equal(response.statusCode, 200);
  assert.equal(response.json().status, "READY");
  assert.deepEqual(response.json().detectedLanguages, ["zh", "ja", "en"]);

  await app.close();
});

test("GET /v1/capture/sessions/:id/summary returns grouped nodes", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "GET",
    url: "/v1/capture/sessions/cap_123/summary"
  });

  assert.equal(response.statusCode, 200);
  const body = response.json();
  assert.equal(body.sessionId, "cap_123");
  assert.equal(body.summaryNodes.length, 1);
  assert.equal(body.summaryNodes[0].type, "SUMMARY");
  assert.deepEqual(body.actionItems, []);

  await app.close();
});

test("POST /v1/agents/query returns a validated grounded answer", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "POST",
    url: "/v1/agents/query",
    headers: { "content-type": "application/json" },
    payload: JSON.stringify({ query: "x" })
  });

  assert.equal(response.statusCode, 200);
  const body = response.json();
  assert.equal(body.answerer, "mock");
  assert.equal(body.query, "x");
  assert.deepEqual(body.contextNodeIds, ["mem_1"]);

  await app.close();
});

test("GET /v1/agents/context/recent returns validated recent context", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "GET",
    url: "/v1/agents/context/recent"
  });

  assert.equal(response.statusCode, 200);
  const body = response.json();
  assert.equal(body.count, 1);
  assert.equal(body.nodes[0].id, "mem_recent");

  await app.close();
});

test("GET /v1/admin/audit-events returns validated audit events", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "GET",
    url: "/v1/admin/audit-events"
  });

  assert.equal(response.statusCode, 200);
  const body = response.json();
  assert.equal(body.length, 1);
  assert.equal(body[0].action, "agent.query");

  await app.close();
});

test("parses a model policy", () => {
  const parsed = modelPolicySchema.parse({
    answerer: "mock",
    asrAdapter: "mock",
    answererOverride: null,
    asrAdapterOverride: null,
    availableAnswerers: ["llm", "mock"],
    availableAsrAdapters: ["mock", "sherpa"],
    llmConfigured: false,
    llmModel: null
  });
  assert.equal(parsed.answerer, "mock");
});

test("GET /v1/admin/model-policy returns the effective policy", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({ method: "GET", url: "/v1/admin/model-policy" });
  assert.equal(response.statusCode, 200);
  assert.equal(response.json().answerer, "mock");
  assert.equal(response.json().llmConfigured, false);
  await app.close();
});

test("PATCH /v1/admin/model-policy sets an override", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "PATCH",
    url: "/v1/admin/model-policy",
    payload: { answerer: "llm" }
  });
  assert.equal(response.statusCode, 200);
  assert.equal(response.json().answererOverride, "llm");
  assert.equal(response.json().answerer, "llm");
  await app.close();
});

test("parses a retention policy", () => {
  const parsed = retentionPolicySchema.parse({
    audioRetentionDays: 30,
    transcriptRetentionDays: 90,
    memoryRetentionDays: 365,
    autoDeleteEnabled: false,
    audioRetentionDaysOverride: null,
    transcriptRetentionDaysOverride: null,
    memoryRetentionDaysOverride: null,
    autoDeleteOverride: null
  });
  assert.equal(parsed.transcriptRetentionDays, 90);
});

test("retention policy rejects a zero-day window", () => {
  assert.throws(() =>
    retentionPolicySchema.parse({
      audioRetentionDays: 0,
      transcriptRetentionDays: 90,
      memoryRetentionDays: 365,
      autoDeleteEnabled: false,
      audioRetentionDaysOverride: null,
      transcriptRetentionDaysOverride: null,
      memoryRetentionDaysOverride: null,
      autoDeleteOverride: null
    })
  );
});

test("GET /v1/admin/retention-policy returns the effective policy", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({ method: "GET", url: "/v1/admin/retention-policy" });
  assert.equal(response.statusCode, 200);
  assert.equal(response.json().audioRetentionDays, 30);
  assert.equal(response.json().autoDeleteEnabled, false);
  await app.close();
});

test("PATCH /v1/admin/retention-policy sets an override", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({
    method: "PATCH",
    url: "/v1/admin/retention-policy",
    payload: { audioRetentionDays: 14 }
  });
  assert.equal(response.statusCode, 200);
  assert.equal(response.json().audioRetentionDaysOverride, 14);
  assert.equal(response.json().audioRetentionDays, 14);
  await app.close();
});

test("GET /v1/admin/users returns validated users", async () => {
  const app = await createApp(createMobileMockClient());
  const response = await app.inject({ method: "GET", url: "/v1/admin/users" });
  assert.equal(response.statusCode, 200);
  assert.equal(response.json()[0].role, "admin");
  await app.close();
});

test("admin routes forward the caller credential instead of inheriting a service credential", async () => {
  const backend = createMobileMockClient();
  const getJson = backend.getJsonFromPython.bind(backend);
  let forwardedAuthorization: string | null = null;
  backend.getJsonFromPython = async <T = unknown>(path: string, init?: RequestInit): Promise<T> => {
    forwardedAuthorization = new Headers(init?.headers).get("Authorization");
    return getJson<T>(path, init);
  };
  const app = await createApp(backend);

  await app.inject({
    method: "GET",
    url: "/v1/admin/users",
    headers: { Authorization: "Bearer caller-token" }
  });

  assert.equal(forwardedAuthorization, "Bearer caller-token");
  await app.close();
});
