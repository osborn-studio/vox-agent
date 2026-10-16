import { afterEach, describe, expect, it, vi } from "vitest";

import {
  claimConversationInvite,
  cancelSpeechStream,
  completeSpeechStream,
  createCaptureSession,
  createConversationSession,
  getCaptureSummary,
  decidePermission,
  getRecent,
  listPendingPermissions,
  processCaptureSession,
  publishConversationLive,
  publishConversationTurn,
  readConversationLive,
  replayConversationTurns,
  replaySpeechEvents,
  resolveBaseUrl,
  resolveConversationBaseUrl,
  resolveDemoMode,
  searchMemory,
  sendSpeechAudioChunk,
  startSpeechStream,
  translateSegments,
  uploadAudio,
} from "./client";
import {
  clearCredential,
  installCredential,
  subscribeCredentialRejected,
} from "../auth/runtime";

// React Native's FormData accepts a { uri, name, type } file descriptor;
// Node/undici's built-in FormData rejects non-Blob values, so we stub a
// permissive one that records what was appended (this is exactly the request
// shape we want to assert). On a real device RN's FormData handles this.
class FakeFormData {
  entries: Array<[string, unknown]> = [];
  append(key: string, value: unknown) {
    this.entries.push([key, value]);
  }
}

const OFFLINE_FAILURE = {
  ok: false,
  offline: true,
  code: "ENGINE_OFFLINE",
} as const;

const CAPTURE_SESSION_RESPONSE = {
  id: "cap_fixture",
  workspaceId: "ws_fixture",
  ownerUserId: "user_fixture",
  edition: "PERSONAL",
  source: "MOBILE",
  mode: "MEETING",
  title: null,
  startedAt: "2026-07-26T12:00:00Z",
  endedAt: null,
  languageHints: ["en"],
  detectedLanguages: ["en"],
  privacyLevel: "PERSONAL",
  consentState: "GRANTED",
  deviceIds: [],
  localOnlyUntilSync: false,
  status: "READY",
  createdAt: "2026-07-26T12:00:00Z",
  updatedAt: "2026-07-26T12:00:00Z",
};

const MEMORY_NODE_RESPONSE = {
  id: "mem_fixture",
  workspaceId: "ws_fixture",
  ownerUserId: "user_fixture",
  sourceSessionId: "cap_fixture",
  type: "SUMMARY",
  title: "Validated summary",
  body: "Loaded from the engine.",
  language: "en",
  importance: "MEDIUM",
  visibility: "PRIVATE",
  tags: [],
  metadata: {},
  confidence: 1,
  createdAt: "2026-07-26T12:00:00Z",
  updatedAt: "2026-07-26T12:00:00Z",
};

function stubOfflineFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("connection refused");
    })
  );
}

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status });
}

afterEach(() => {
  clearCredential();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("resolveBaseUrl", () => {
  it("uses an explicit LAN engine URL without a trailing slash", () => {
    expect(resolveBaseUrl("http://192.168.1.23:18900/")).toBe(
      "http://192.168.1.23:18900"
    );
  });

  it("rejects credentials and non-http engine URLs", () => {
    expect(resolveBaseUrl("http://user:secret@192.168.1.23:18900")).toBe(
      "http://127.0.0.1:18900"
    );
    expect(resolveBaseUrl("file:///tmp/engine.sock")).toBe(
      "http://127.0.0.1:18900"
    );
  });

  it("keeps the public conversation ingress separate from the private engine origin", () => {
    expect(
      resolveConversationBaseUrl(
        "https://conversation.example.test/",
        "http://192.168.1.23:18900"
      )
    ).toBe("https://conversation.example.test");
    expect(
      resolveConversationBaseUrl(
        "https://guest:secret@conversation.example.test",
        "http://192.168.1.23:18900"
      )
    ).toBe("http://192.168.1.23:18900");
  });

  it("routes host creation privately and guest claim through the allowlisted origin", async () => {
    vi.stubEnv("EXPO_PUBLIC_VOX_ENGINE_URL", "https://private.example.test");
    vi.stubEnv(
      "EXPO_PUBLIC_VOX_CONVERSATION_URL",
      "https://conversation.example.test"
    );
    vi.resetModules();
    const isolatedClient = await import("./client");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ created: true }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ claimed: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await isolatedClient.createConversationSession({
      mode: "REMOTE_QR",
      hostLanguage: "en",
      guestLanguage: "zh-CN",
    });
    await isolatedClient.claimConversationInvite(
      "invite-token-with-enough-entropy-123"
    );

    expect(fetchMock.mock.calls[0][0]).toBe(
      "https://private.example.test/v1/conversation-sessions"
    );
    expect(fetchMock.mock.calls[1][0]).toBe(
      "https://conversation.example.test/public/v1/conversation-sessions/claim"
    );
  });
});

describe("release data boundary", () => {
  it("enables demo data only for an explicit true flag", () => {
    expect(resolveDemoMode(undefined)).toBe(false);
    expect(resolveDemoMode("false")).toBe(false);
    expect(resolveDemoMode("1")).toBe(false);
    expect(resolveDemoMode("TRUE")).toBe(false);
    expect(resolveDemoMode("true")).toBe(true);
  });

  it("fails closed without seeded reads or fabricated writes by default", async () => {
    vi.stubEnv("EXPO_PUBLIC_VOX_DEMO_MODE", "false");
    vi.resetModules();
    const isolatedClient = await import("./client");
    stubOfflineFetch();

    const recent = await isolatedClient.getRecent();
    const capture = await isolatedClient.createCaptureSession({
      mode: "MEETING",
    });
    const pairing = await isolatedClient.createPairingCode();
    const balance = await isolatedClient.getTokenBalance();
    const answer = await isolatedClient.askAgent("What did I promise?");

    for (const result of [recent, capture, pairing, balance, answer]) {
      expect(result).toEqual(OFFLINE_FAILURE);
    }
    expect(
      JSON.stringify({ recent, capture, pairing, balance, answer })
    ).not.toMatch(/mock_|MOCK42|5000|offline placeholder/i);
  });

  it("labels successful reads from an explicit demo build without bundling seeds", async () => {
    vi.stubEnv("EXPO_PUBLIC_VOX_DEMO_MODE", "true");
    vi.resetModules();
    const isolatedClient = await import("./client");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([
          { ...CAPTURE_SESSION_RESPONSE, id: "demo-engine-session" },
        ])
      )
      .mockResolvedValueOnce(
        jsonResponse([{ ...MEMORY_NODE_RESPONSE, id: "demo-engine-memory" }])
      );
    vi.stubGlobal("fetch", fetchMock);

    const recent = await isolatedClient.getRecent();

    expect(recent.ok).toBe(true);
    if (!recent.ok) throw new Error("expected demo-engine data");
    expect(recent.source).toBe("demo");
    expect(recent.offline).toBe(false);
    expect(recent.data.sessions[0].id).toBe("demo-engine-session");
    expect(recent.data.nodes[0].id).toBe("demo-engine-memory");
  });

  it("fails reads closed when the configured demo engine is offline", async () => {
    vi.stubEnv("EXPO_PUBLIC_VOX_DEMO_MODE", "true");
    vi.resetModules();
    const isolatedClient = await import("./client");
    stubOfflineFetch();

    const recent = await isolatedClient.getRecent();

    expect(recent).toEqual(OFFLINE_FAILURE);
    expect(JSON.stringify(recent)).not.toContain("demo-engine");
  });

  it("does not hide a server rejection behind demo data", async () => {
    vi.stubEnv("EXPO_PUBLIC_VOX_DEMO_MODE", "true");
    vi.resetModules();
    const isolatedClient = await import("./client");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("unauthorized", { status: 401 }))
    );

    const recent = await isolatedClient.getRecent();

    expect(recent).toEqual({
      ok: false,
      offline: false,
      status: 401,
      code: "AUTH_REQUIRED",
    });
    expect(JSON.stringify(recent)).not.toContain("demo_");
  });

  it("returns live recent sessions and memory with an explicit source", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([{ ...CAPTURE_SESSION_RESPONSE, id: "cap_live" }])
      )
      .mockResolvedValueOnce(
        jsonResponse([{ ...MEMORY_NODE_RESPONSE, id: "mem_live" }])
      );
    vi.stubGlobal("fetch", fetchMock);

    const recent = await getRecent();

    expect(recent.ok).toBe(true);
    if (!recent.ok) throw new Error("expected live recent data");
    expect(recent.source).toBe("live");
    expect(recent.offline).toBe(false);
    expect(recent.data.sessions[0].id).toBe("cap_live");
    expect(recent.data.nodes[0].id).toBe("mem_live");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:18900/v1/capture/sessions?limit=50",
      "http://127.0.0.1:18900/v1/memory/recent",
    ]);
  });

  it("never fabricates mutation success, even in demo mode", async () => {
    vi.stubEnv("EXPO_PUBLIC_VOX_DEMO_MODE", "true");
    vi.resetModules();
    const isolatedClient = await import("./client");
    stubOfflineFetch();

    const capture = await isolatedClient.createCaptureSession({
      mode: "MEETING",
    });
    const pairing = await isolatedClient.createPairingCode();
    const answer = await isolatedClient.askAgent("Summarize today.");

    expect(capture.ok).toBe(false);
    expect(pairing.ok).toBe(false);
    expect(answer.ok).toBe(false);
    expect(JSON.stringify({ capture, pairing, answer })).not.toMatch(
      /mock_|MOCK42|offline placeholder/i
    );
  });

  it("labels a real demo-engine mutation response as demo", async () => {
    vi.stubEnv("EXPO_PUBLIC_VOX_DEMO_MODE", "true");
    vi.resetModules();
    const isolatedClient = await import("./client");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          { ...CAPTURE_SESSION_RESPONSE, id: "demo-engine-capture" },
          201
        )
      )
    );

    const result = await isolatedClient.createCaptureSession({
      mode: "MEETING",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected demo-engine success");
    expect(result.source).toBe("demo");
    expect(result.offline).toBe(false);
    expect(result.data.id).toBe("demo-engine-capture");
  });

  it("creates capture sessions without client-supplied workspace or owner identity", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(CAPTURE_SESSION_RESPONSE, 201)
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createCaptureSession({
      mode: "MEETING",
      languageHints: ["en"],
    });

    expect(result.ok).toBe(true);
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    const body = JSON.parse(init.body as string);
    expect(body.workspaceId).toBeUndefined();
    expect(body.ownerUserId).toBeUndefined();
    expect(body).toMatchObject({
      source: "MOBILE",
      mode: "MEETING",
      languageHints: ["en"],
    });
  });

  it("injects the principal credential into authenticated transports only", async () => {
    vi.stubGlobal("FormData", FakeFormData);
    installCredential("member-token-with-enough-entropy");
    const createdConversation = {
      session: {
        schema: "voxagent.conversation.session",
        schemaVersion: 1,
        id: "conv_auth",
        mode: "REMOTE_QR",
        status: "WAITING",
        hostLanguage: "en",
        guestLanguage: "zh-CN",
        participantCount: 1,
        createdAt: "2026-07-26T12:00:00Z",
        expiresAt: "2026-07-26T13:00:00Z",
        nextSequence: 1,
      },
      hostAccessToken: "host-token-with-enough-entropy-123",
      localGuestAccessToken: null,
      invite: {
        inviteToken: "invite-token-with-enough-entropy-123",
        qrPayload:
          "voxagent://conversation/join#v=1&token=invite-token-with-enough-entropy-123",
        expiresAt: "2026-07-26T12:05:00Z",
      },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(CAPTURE_SESSION_RESPONSE, 201))
      .mockResolvedValueOnce(
        jsonResponse({ query: "launch", results: [] })
      )
      .mockResolvedValueOnce(
        jsonResponse({
          sessionId: "cap_fixture",
          audioPath: "/audio/cap_fixture.m4a",
          bytes: 42,
        })
      )
      .mockResolvedValueOnce(jsonResponse(createdConversation, 201))
      .mockResolvedValueOnce(
        jsonResponse({
          session: {
            ...createdConversation.session,
            status: "ACTIVE",
            participantCount: 2,
          },
          participantRole: "GUEST",
          accessToken: "guest-token-with-enough-entropy-123",
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    await createCaptureSession({ mode: "MEETING" });
    await searchMemory("launch");
    await uploadAudio("cap_fixture", "file:///capture.m4a");
    await createConversationSession({
      mode: "REMOTE_QR",
      hostLanguage: "en",
      guestLanguage: "zh-CN",
    });
    await claimConversationInvite("invite-token-with-enough-entropy-123");

    const authorization = fetchMock.mock.calls.map(([, init]) =>
      new Headers(init?.headers).get("Authorization")
    );
    expect(authorization).toEqual([
      "Bearer member-token-with-enough-entropy",
      "Bearer member-token-with-enough-entropy",
      "Bearer member-token-with-enough-entropy",
      "Bearer member-token-with-enough-entropy",
      null,
    ]);
  });

  it("classifies a revoked principal credential and emits no secret data", async () => {
    installCredential("revoked-token-with-enough-entropy");
    const rejected = vi.fn();
    const unsubscribe = subscribeCredentialRejected(rejected);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "invalid" }, 401))
    );

    const result = await createCaptureSession({ mode: "MEETING" });
    unsubscribe();

    expect(result).toEqual({
      ok: false,
      offline: false,
      status: 401,
      code: "AUTH_REQUIRED",
    });
    expect(rejected).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(result)).not.toContain("revoked-token");
  });
});

describe("principal-scoped permission client", () => {
  const permissionRequest = {
    approval_id: "approval-mobile-1",
    task_id: "task-mobile-1",
    agent_id: "agent-mobile-1",
    tool: "write",
    scope: {
      action: "write",
      path: "/tmp/report.md",
    },
    requested_at: "2026-07-26T12:00:00Z",
    expires_at: "2026-07-26T12:02:00Z",
  };

  it("uses the installed principal and sends an explicit non-persistent decision", async () => {
    installCredential("mobile-principal-token-with-entropy");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([permissionRequest]))
      .mockResolvedValueOnce(
        jsonResponse({ ok: true, decision: "approved" })
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listPendingPermissions()).resolves.toMatchObject({
      ok: true,
      data: [permissionRequest],
    });
    await expect(
      decidePermission(permissionRequest.approval_id, true)
    ).resolves.toMatchObject({
      ok: true,
      data: { ok: true, decision: "approved" },
    });

    const [listUrl, listInit] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    const [decisionUrl, decisionInit] = fetchMock.mock.calls[1] as unknown as [
      string,
      RequestInit,
    ];
    expect(listUrl).toBe(
      "http://127.0.0.1:18900/v1/permissions/pending"
    );
    expect(decisionUrl).toBe(
      "http://127.0.0.1:18900/v1/permissions/approve"
    );
    expect(new Headers(listInit.headers).get("Authorization")).toBe(
      "Bearer mobile-principal-token-with-entropy"
    );
    expect(new Headers(decisionInit.headers).get("Authorization")).toBe(
      "Bearer mobile-principal-token-with-entropy"
    );
    expect(decisionInit.method).toBe("POST");
    expect(JSON.parse(decisionInit.body as string)).toEqual({
      approval_id: permissionRequest.approval_id,
      approved: true,
      remember_scope: false,
    });
  });

  it("rejects unsafe request identities before fetch and exposes a uniform 404", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ detail: "not found" }, 404)
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(decidePermission(" approval-1 ", true)).resolves.toEqual({
      ok: false,
      offline: false,
      code: "REQUEST_REJECTED",
    });
    expect(fetchMock).not.toHaveBeenCalled();

    await expect(decidePermission("approval-1", false)).resolves.toEqual({
      ok: false,
      offline: false,
      status: 404,
      code: "REQUEST_REJECTED",
    });
  });

  it("fails closed when a pending or decision response has the wrong shape", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([{ ...permissionRequest, expires_at: "tomorrow" }])
      )
      .mockResolvedValueOnce(
        jsonResponse({ ok: true, decision: "maybe" })
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listPendingPermissions()).resolves.toMatchObject({
      ok: false,
      code: "INVALID_RESPONSE",
    });
    await expect(decidePermission("approval-1", true)).resolves.toMatchObject({
      ok: false,
      code: "INVALID_RESPONSE",
    });
  });
});

describe("authenticated speech PCM client", () => {
  it("rejects unsafe speech metadata before fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      startSpeechStream({
        sessionId: " conversation-1 ",
        languageHints: ["not a language"],
      })
    ).resolves.toMatchObject({ ok: false, code: "REQUEST_REJECTED" });
    await expect(
      sendSpeechAudioChunk({
        streamId: "speech-mobile-1",
        resumeToken: "resume-token-with-enough-entropy",
        chunkIndex: 0,
        sampleRate: 16_000,
        channels: 1,
        audio: Uint8Array.from([1]).buffer,
        signal: new AbortController().signal,
      })
    ).resolves.toMatchObject({ ok: false, code: "REQUEST_REJECTED" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("starts a stream and sends strict binary metadata with principal auth", async () => {
    installCredential("mobile-principal-token-with-entropy");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(
          {
            streamId: "speech-mobile-1",
            resumeToken: "resume-token-with-enough-entropy",
            schema: "voxagent.speech.event",
            schemaVersion: 1,
            nextSequence: 2,
          },
          201
        )
      )
      .mockResolvedValueOnce(
        jsonResponse({
          schema: "voxagent.speech.audio-chunk",
          schemaVersion: 1,
          streamId: "speech-mobile-1",
          chunkIndex: 0,
          acceptedBytes: 4,
          totalBytes: 4,
          durationMs: 0,
          nextChunkIndex: 1,
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const started = await startSpeechStream({
      sessionId: "conversation-1",
      languageHints: ["en", "zh-CN"],
    });
    const audio = Uint8Array.from([1, 0, 2, 0]).buffer;
    const sent = await sendSpeechAudioChunk({
      streamId: "speech-mobile-1",
      resumeToken: "resume-token-with-enough-entropy",
      chunkIndex: 0,
      sampleRate: 16_000,
      channels: 1,
      audio,
      signal: new AbortController().signal,
    });

    expect(started.ok).toBe(true);
    expect(sent.ok).toBe(true);
    const [, startInit] = fetchMock.mock.calls[0];
    expect(new Headers(startInit.headers).get("Authorization")).toBe(
      "Bearer mobile-principal-token-with-entropy"
    );
    const [, chunkInit] = fetchMock.mock.calls[1];
    const headers = new Headers(chunkInit.headers);
    expect(headers.get("Authorization")).toBe(
      "Bearer mobile-principal-token-with-entropy"
    );
    expect(headers.get("X-VoxAgent-Stream-Token")).toBe(
      "resume-token-with-enough-entropy"
    );
    expect(headers.get("X-VoxAgent-Audio-Chunk-Index")).toBe("0");
    expect(headers.get("X-VoxAgent-Audio-Sample-Rate")).toBe("16000");
    expect(headers.get("X-VoxAgent-Audio-Channels")).toBe("1");
    expect(headers.get("X-VoxAgent-Audio-Encoding")).toBe("pcm_s16le");
    expect(Array.from(new Uint8Array(chunkInit.body as ArrayBuffer))).toEqual([
      1, 0, 2, 0,
    ]);
  });

  it("aborts a chunk request and rejects malformed acknowledgements", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        return await new Promise<Response>((resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(new Error("aborted")),
            { once: true }
          );
        });
      })
    );
    const pending = sendSpeechAudioChunk({
      streamId: "speech-mobile-1",
      resumeToken: "resume-token-with-enough-entropy",
      chunkIndex: 0,
      sampleRate: 16_000,
      channels: 1,
      audio: Uint8Array.from([1, 0]).buffer,
      signal: controller.signal,
    });
    controller.abort();
    await expect(pending).resolves.toEqual(OFFLINE_FAILURE);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          schema: "voxagent.speech.audio-chunk",
          schemaVersion: 1,
          streamId: "speech-mobile-1",
          chunkIndex: 0,
          acceptedBytes: 2,
          totalBytes: 2,
          durationMs: 0,
          nextChunkIndex: 9,
        })
      )
    );
    await expect(
      sendSpeechAudioChunk({
        streamId: "speech-mobile-1",
        resumeToken: "resume-token-with-enough-entropy",
        chunkIndex: 0,
        sampleRate: 16_000,
        channels: 1,
        audio: Uint8Array.from([1, 0]).buffer,
        signal: new AbortController().signal,
      })
    ).resolves.toMatchObject({ ok: false, code: "INVALID_RESPONSE" });
  });

  it("completes and cancels with stable mobile idempotency keys", async () => {
    const event = {
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
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(event))
      .mockResolvedValueOnce(
        jsonResponse({
          ...event,
          eventId: "terminal-2",
          type: "stream.cancelled",
          payload: { reason: "user_cancelled", cancelledBy: "mobile" },
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    expect(
      (
        await completeSpeechStream(
          "speech-mobile-1",
          "resume-token-with-enough-entropy"
        )
      ).ok
    ).toBe(true);
    expect(
      (
        await cancelSpeechStream(
          "speech-mobile-1",
          "resume-token-with-enough-entropy"
        )
      ).ok
    ).toBe(true);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      reason: "input_finished",
      idempotencyKey: "mobile-complete-speech-mobile-1",
    });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toEqual({
      reason: "user_cancelled",
      cancelledBy: "mobile",
      idempotencyKey: "mobile-cancel-speech-mobile-1",
    });
  });
});

describe("runtime response validation", () => {
  it("rejects shape-invalid 2xx data across every transport helper", async () => {
    vi.stubGlobal("FormData", FakeFormData);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          results: [{ node: { id: "partial" }, score: "not-a-number" }],
        })
      )
      .mockResolvedValueOnce(jsonResponse({ id: "capture-only" }, 201))
      .mockResolvedValueOnce(
        jsonResponse({ sessionId: "cap_1", audioPath: "/x", bytes: "42" })
      )
      .mockResolvedValueOnce(
        jsonResponse({
          session: {},
          hostAccessToken: 42,
          localGuestAccessToken: null,
          invite: null,
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const results = [
      await searchMemory("invalid"),
      await createCaptureSession({ mode: "MEETING" }),
      await uploadAudio("cap_1", "file:///tmp/rec.m4a"),
      await createConversationSession({
        mode: "FACE_TO_FACE",
        hostLanguage: "en",
        guestLanguage: "zh-CN",
      }),
    ];

    for (const result of results) {
      expect(result).toEqual({
        ok: false,
        offline: false,
        code: "INVALID_RESPONSE",
      });
      expect("data" in result).toBe(false);
    }
  });
});

describe("uploadAudio", () => {
  function stubFormData() {
    vi.stubGlobal("FormData", FakeFormData);
  }

  it("POSTs multipart form-data to the session audio endpoint and returns a live result", async () => {
    stubFormData();
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ sessionId: "cap_1", audioPath: "/x/cap_1.m4a", bytes: 42 }),
          { status: 200 }
        )
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await uploadAudio("cap_1", "file:///tmp/rec.m4a");

    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected a live result");
    expect(result.data.bytes).toBe(42);

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:18900/v1/capture/sessions/cap_1/audio");
    expect(init.method).toBe("POST");
    // no explicit Content-Type — fetch derives the multipart boundary from the body
    expect(new Headers(init.headers).get("Content-Type")).toBeNull();

    const body = init.body as unknown as FakeFormData;
    expect(body).toBeInstanceOf(FakeFormData);
    const [field, file] = body.entries[0] as [string, { uri: string; name: string; type: string }];
    expect(field).toBe("file");
    expect(file.uri).toBe("file:///tmp/rec.m4a");
    expect(file.name).toBe("recording.m4a");
    expect(file.type).toBe("audio/m4a");
  });

  it("honors an explicit file name and mime type and url-encodes the session id", async () => {
    stubFormData();
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ sessionId: "a/b", audioPath: "", bytes: 1 }), { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    await uploadAudio("a/b", "file:///r.wav", { fileName: "meeting.wav", mimeType: "audio/wav" });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:18900/v1/capture/sessions/a%2Fb/audio");
    const file = (init.body as unknown as FakeFormData).entries[0][1] as { name: string; type: string };
    expect(file.name).toBe("meeting.wav");
    expect(file.type).toBe("audio/wav");
  });

  it("returns a server-error result (not a fake success) on non-2xx", async () => {
    stubFormData();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("not found", { status: 404 })));

    const result = await uploadAudio("cap_missing", "file:///tmp/rec.m4a");

    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected a failure result");
    expect(result.offline).toBe(false);
    expect(result.status).toBe(404);
    expect("data" in result).toBe(false);
  });

  it("returns an offline result on network failure", async () => {
    stubFormData();
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("connection refused");
    }));

    const result = await uploadAudio("cap_1", "file:///tmp/rec.m4a");

    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected a failure result");
    expect(result.offline).toBe(true);
    expect(result.status).toBeUndefined();
  });
});

describe("capture processing", () => {
  it("coalesces concurrent process requests for the same session", async () => {
    let resolveResponse!: (response: Response) => void;
    const response = new Promise<Response>((resolve) => {
      resolveResponse = resolve;
    });
    const fetchMock = vi.fn(() => response);
    vi.stubGlobal("fetch", fetchMock);

    const first = processCaptureSession("cap_fixture");
    const duplicate = processCaptureSession("cap_fixture");
    expect(duplicate).toBe(first);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolveResponse(jsonResponse(CAPTURE_SESSION_RESPONSE));
    const [firstResult, duplicateResult] = await Promise.all([first, duplicate]);

    expect(firstResult).toEqual(duplicateResult);
    expect(firstResult.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(
      "http://127.0.0.1:18900/v1/capture/sessions/cap_fixture/process"
    );
    expect(init.method).toBe("POST");
    expect(init.body).toBe("{}");
  });

  it("clears a settled failure so an explicit retry reaches the engine", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("provider unavailable", { status: 503 }))
      .mockResolvedValueOnce(jsonResponse(CAPTURE_SESSION_RESPONSE));
    vi.stubGlobal("fetch", fetchMock);

    const failed = await processCaptureSession("cap_fixture");
    const retried = await processCaptureSession("cap_fixture");

    expect(failed).toMatchObject({
      ok: false,
      offline: false,
      status: 503,
      code: "REQUEST_REJECTED",
    });
    expect(retried.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reports an offline process failure without discarding retryability", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("connection refused");
      })
    );

    expect(await processCaptureSession("cap_fixture")).toEqual({
      ok: false,
      offline: true,
      code: "ENGINE_OFFLINE",
    });
  });

  it("rejects a process response that has not converged to READY", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...CAPTURE_SESSION_RESPONSE, status: "PROCESSING" })
      )
    );

    const result = await processCaptureSession("cap_fixture");

    expect(result).toEqual({
      ok: false,
      offline: false,
      code: "INVALID_RESPONSE",
    });
  });

  it("rejects a process response for a different capture session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...CAPTURE_SESSION_RESPONSE, id: "cap_foreign" })
      )
    );

    expect(await processCaptureSession("cap_fixture")).toEqual({
      ok: false,
      offline: false,
      code: "INVALID_RESPONSE",
    });
  });

  it("loads a validated same-session summary and encodes the session id", async () => {
    const summary = {
      sessionId: "a/b",
      status: "READY",
      summaryNodes: [{ ...MEMORY_NODE_RESPONSE, sourceSessionId: "a/b" }],
      actionItems: [],
      decisions: [],
    };
    const fetchMock = vi.fn(async (_input: string | URL | Request) =>
      jsonResponse(summary)
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getCaptureSummary("a/b");

    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected a validated summary");
    expect(result.data).toEqual(summary);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://127.0.0.1:18900/v1/capture/sessions/a%2Fb/summary"
    );
  });

  it("fails closed on malformed summary data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          sessionId: "cap_fixture",
          status: "READY",
          summaryNodes: [{ id: "partial" }],
          actionItems: [],
          decisions: [],
        })
      )
    );

    expect(await getCaptureSummary("cap_fixture")).toEqual({
      ok: false,
      offline: false,
      code: "INVALID_RESPONSE",
    });
  });

  it("rejects summary nodes that are not bound to the requested session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          sessionId: "cap_fixture",
          status: "READY",
          summaryNodes: [
            { ...MEMORY_NODE_RESPONSE, sourceSessionId: "cap_foreign" },
          ],
          actionItems: [],
          decisions: [],
        })
      )
    );

    expect(await getCaptureSummary("cap_fixture")).toEqual({
      ok: false,
      offline: false,
      code: "INVALID_RESPONSE",
    });
  });
});

describe("translateSegments", () => {
  it("POSTs cited segments with explicit remote consent and returns a final event", async () => {
    const event = {
      schema: "voxagent.translation.event",
      schemaVersion: 1,
      eventId: "translation_event_1",
      translationId: "translation_1",
      type: "translation.final",
      emittedAt: "2026-07-18T12:00:00Z",
      latencyMs: 83,
      payload: {
        sourceLanguage: "ja",
        targetLanguage: "en",
        text: "Tomorrow's meeting is at ten.",
        confidence: 0.94,
        provider: "openai-compatible",
        model: "translation-model",
        sourceSegments: [
          {
            streamId: "speech_1",
            segmentId: "segment-1",
            revision: 1,
            audioStartMs: 0,
            audioEndMs: 800,
          },
        ],
        glossaryHits: [{ sourceText: "会議", targetText: "meeting" }],
      },
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(event), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await translateSegments({
      sourceLanguage: "ja",
      targetLanguage: "en",
      segments: [
        {
          streamId: "speech_1",
          segmentId: "segment-1",
          revision: 1,
        },
      ],
      glossary: [{ sourceText: "会議", targetText: "meeting" }],
      allowRemote: true,
      idempotencyKey: "mobile-translation-1",
    }, "resume-token-with-enough-entropy");

    expect(result.ok).toBe(true);
    if (!result.ok || result.data.type !== "translation.final") {
      throw new Error("expected final translation");
    }
    expect(result.data.payload.sourceSegments[0].segmentId).toBe("segment-1");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:18900/v1/translations");
    expect(JSON.parse(init.body as string).allowRemote).toBe(true);
    expect(JSON.parse(init.body as string).segments[0].text).toBeUndefined();
    expect(new Headers(init.headers).get("X-VoxAgent-Stream-Token")).toBe(
      "resume-token-with-enough-entropy"
    );
  });

  it("returns an explicit failure event when the engine is offline", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("connection refused");
    }));

    const result = await translateSegments({
      sourceLanguage: "en",
      targetLanguage: "ja",
      segments: [
        {
          streamId: "speech_1",
          segmentId: "segment-private",
          revision: 1,
        },
      ],
      glossary: [],
      allowRemote: false,
      idempotencyKey: "mobile-translation-offline",
    }, "resume-token-with-enough-entropy");

    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected an offline result");
    expect(result.offline).toBe(true);
    expect(result.code).toBe("ENGINE_OFFLINE");
    expect("data" in result).toBe(false);
    expect(JSON.stringify(result)).not.toContain("sourceText");
  });
});

describe("speech event replay", () => {
  it("validates ordered events and keeps both principal and stream capability in headers", async () => {
    installCredential("mobile-principal-token-with-entropy");
    const streamToken = "stream-token-with-enough-entropy-123";
    const payload = {
      streamId: "speech/123",
      events: [
        {
          schema: "voxagent.speech.event",
          schemaVersion: 1,
          eventId: "speech-started-1",
          streamId: "speech/123",
          sessionId: "conv_123",
          sequence: 1,
          type: "stream.started",
          monotonicMs: 0,
          emittedAt: "2026-07-26T12:00:00Z",
          payload: { languageHints: ["en"] },
        },
      ],
      nextSequence: 2,
      terminal: false,
    };
    const fetchMock = vi.fn(async () => jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);

    const result = await replaySpeechEvents("speech/123", streamToken, 0);

    expect(result.ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(
      "http://127.0.0.1:18900/v1/speech/streams/speech%2F123/events?afterSequence=0"
    );
    expect(url).not.toContain(streamToken);
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe(
      "Bearer mobile-principal-token-with-entropy"
    );
    expect(headers.get("X-VoxAgent-Stream-Token")).toBe(streamToken);
  });

  it("rejects drifted, unordered, or cross-stream replay events", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        streamId: "speech_123",
        events: [
          {
            schema: "voxagent.speech.event",
            schemaVersion: 1,
            eventId: "speech-started-2",
            streamId: "speech_foreign",
            sessionId: "conv_123",
            sequence: 2,
            type: "stream.started",
            monotonicMs: 0,
            emittedAt: "2026-07-26T12:00:00Z",
            payload: { languageHints: ["en"] },
          },
        ],
        nextSequence: 2,
        terminal: false,
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    expect(
      await replaySpeechEvents(
        "speech_123",
        "stream-token-with-enough-entropy-123",
        0
      )
    ).toEqual({
      ok: false,
      offline: false,
      code: "INVALID_RESPONSE",
    });
  });
});

describe("conversation sessions", () => {
  const session = {
    schema: "voxagent.conversation.session" as const,
    schemaVersion: 1 as const,
    id: "conv_123",
    mode: "REMOTE_QR" as const,
    status: "WAITING" as const,
    hostLanguage: "en",
    guestLanguage: "zh-CN",
    participantCount: 1,
    createdAt: "2026-07-19T12:00:00Z",
    expiresAt: "2026-07-19T13:00:00Z",
    nextSequence: 1,
  };

  it("creates from authenticated product fields without body-trusted owner IDs", async () => {
    installCredential("verified-host-with-enough-entropy");
    const created = {
      session,
      hostAccessToken: "host-token-with-enough-entropy-123",
      localGuestAccessToken: null,
      invite: {
        inviteToken: "invite-token-with-enough-entropy-123",
        qrPayload:
          "voxagent://conversation/join#v=1&token=invite-token-with-enough-entropy-123",
        expiresAt: "2026-07-19T12:05:00Z",
      },
    };
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(created), { status: 201 })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createConversationSession({
      mode: "REMOTE_QR",
      hostLanguage: "en",
      guestLanguage: "zh-CN",
    });

    expect(result.ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:18900/v1/conversation-sessions");
    expect(new Headers(init.headers).get("Authorization")).toBe(
      "Bearer verified-host-with-enough-entropy"
    );
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      mode: "REMOTE_QR",
      hostLanguage: "en",
      guestLanguage: "zh-CN",
    });
    expect(body.ownerUserId).toBeUndefined();
    expect(body.workspaceId).toBeUndefined();
  });

  it("claims an invite in the body and replays with an in-memory header capability", async () => {
    const guestToken = "guest-token-with-enough-entropy-123";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            session: { ...session, status: "ACTIVE", participantCount: 2 },
            participantRole: "GUEST",
            accessToken: guestToken,
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessionId: "conv_123",
            turns: [],
            nextSequence: 1,
            status: "ACTIVE",
          }),
          { status: 200 }
        )
      );
    vi.stubGlobal("fetch", fetchMock);

    const claimed = await claimConversationInvite(
      "invite-token-with-enough-entropy-123"
    );
    const replay = await replayConversationTurns("conv/123", guestToken, 0);

    expect(claimed.ok).toBe(true);
    expect(replay.ok).toBe(true);
    const [claimUrl, claimInit] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(claimUrl).not.toContain("invite-token");
    expect(JSON.parse(claimInit.body as string).inviteToken).toBe(
      "invite-token-with-enough-entropy-123"
    );
    const [replayUrl, replayInit] = fetchMock.mock.calls[1] as unknown as [
      string,
      RequestInit,
    ];
    expect(replayUrl).toBe(
      "http://127.0.0.1:18900/public/v1/conversation-sessions/conv%2F123/turns?afterSequence=0"
    );
    expect(replayUrl).not.toContain(guestToken);
    expect(
      new Headers(replayInit.headers).get("X-VoxAgent-Conversation-Token")
    ).toBe(guestToken);
    expect(new Headers(replayInit.headers).get("Authorization")).toBeNull();
  });

  it("fails explicitly without fabricating a conversation or secret", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network offline");
      })
    );

    const result = await claimConversationInvite(
      "invite-token-with-enough-entropy-123"
    );

    expect(result).toEqual({
      ok: false,
      offline: true,
      code: "ENGINE_OFFLINE",
    });
    expect(JSON.stringify(result)).not.toContain("invite-token");
  });

  it("reads the bounded live snapshot with only the in-memory participant capability", async () => {
    const participantToken = "participant-token-with-enough-entropy-123";
    const liveSnapshot = {
      sessionId: session.id,
      host: {
        schema: "voxagent.conversation.live",
        schemaVersion: 1,
        type: "conversation.live",
        sessionId: session.id,
        participantRole: "HOST",
        streamId: "speech-stream-1",
        segmentId: "segment-1",
        revision: 2,
        sourceLanguage: "en",
        targetLanguage: "zh-CN",
        speechEvent: {
          schema: "voxagent.speech.event",
          schemaVersion: 1,
          eventId: "speech-partial-2",
          type: "transcript.partial",
          streamId: "speech-stream-1",
          sessionId: session.id,
          sequence: 2,
          monotonicMs: 800,
          emittedAt: "2026-07-19T12:00:01Z",
          payload: {
            segmentId: "segment-1",
            revision: 2,
            text: "Hello there",
            language: { code: "en", confidence: 0.98 },
            speaker: { id: "speaker-host", label: null },
            audioStartMs: 100,
            audioEndMs: 800,
            confidence: 0.96,
          },
        },
        updatedAt: "2026-07-19T12:00:01Z",
      },
      guest: null,
      status: "ACTIVE",
    };
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(liveSnapshot), { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await readConversationLive(
      "conv/123",
      participantToken
    );

    expect(result.ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(
      "http://127.0.0.1:18900/public/v1/conversation-sessions/conv%2F123/live"
    );
    expect(url).not.toContain(participantToken);
    const headers = new Headers(init.headers);
    expect(headers.get("X-VoxAgent-Conversation-Token")).toBe(participantToken);
    expect(headers.get("Authorization")).toBeNull();
  });

  it("publishes a partial through the participant capability without a principal header", async () => {
    const participantToken = "participant-token-with-enough-entropy-123";
    const speechEvent = {
      schema: "voxagent.speech.event" as const,
      schemaVersion: 1 as const,
      eventId: "speech-partial-1",
      type: "transcript.partial" as const,
      streamId: "speech-stream-1",
      sessionId: session.id,
      sequence: 2,
      monotonicMs: 800,
      emittedAt: "2026-07-19T12:00:01Z",
      payload: {
        segmentId: "segment-1",
        revision: 1,
        text: "Hello",
        language: { code: "en", confidence: 0.98 },
        speaker: { id: "speaker-host", label: null },
        audioStartMs: 0,
        audioEndMs: 800,
        confidence: 0.96,
      },
    };
    const draft = {
      schema: "voxagent.conversation.live",
      schemaVersion: 1,
      type: "conversation.live",
      sessionId: session.id,
      participantRole: "HOST",
      streamId: speechEvent.streamId,
      segmentId: speechEvent.payload.segmentId,
      revision: speechEvent.payload.revision,
      sourceLanguage: "en",
      targetLanguage: "zh-CN",
      speechEvent,
      updatedAt: "2026-07-19T12:00:02Z",
    };
    const fetchMock = vi.fn(async () => jsonResponse(draft));
    vi.stubGlobal("fetch", fetchMock);

    const result = await publishConversationLive(
      session.id,
      participantToken,
      speechEvent
    );

    expect(result.ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(
      "http://127.0.0.1:18900/public/v1/conversation-sessions/conv_123/live"
    );
    expect(url).not.toContain(participantToken);
    const headers = new Headers(init.headers);
    expect(headers.get("X-VoxAgent-Conversation-Token")).toBe(participantToken);
    expect(headers.get("Authorization")).toBeNull();
    expect(JSON.parse(init.body as string)).toEqual({ speechEvent });
  });

  it("publishes one final speech and cited translation pair with only the participant capability", async () => {
    const participantToken = "participant-token-with-enough-entropy-123";
    const speechEvent = {
      schema: "voxagent.speech.event" as const,
      schemaVersion: 1 as const,
      eventId: "speech-event-1",
      type: "transcript.final" as const,
      streamId: "speech-stream-1",
      sessionId: session.id,
      sequence: 2,
      monotonicMs: 1_200,
      emittedAt: "2026-07-19T12:00:01Z",
      payload: {
        segmentId: "segment-1",
        revision: 1,
        text: "Hello",
        language: { code: "en", confidence: 0.98 },
        speaker: { id: "speaker-host", label: null },
        audioStartMs: 100,
        audioEndMs: 900,
        confidence: 0.96,
      },
    };
    const translationEvent = {
      schema: "voxagent.translation.event" as const,
      schemaVersion: 1 as const,
      eventId: "translation-event-1",
      translationId: "translation-1",
      type: "translation.final" as const,
      emittedAt: "2026-07-19T12:00:02Z",
      latencyMs: 80,
      payload: {
        sourceLanguage: "en",
        targetLanguage: "zh-CN",
        text: "你好",
        confidence: 0.95,
        provider: "local",
        model: "translation-model",
        sourceSegments: [
          {
            streamId: speechEvent.streamId,
            segmentId: speechEvent.payload.segmentId,
            revision: speechEvent.payload.revision,
            audioStartMs: speechEvent.payload.audioStartMs,
            audioEndMs: speechEvent.payload.audioEndMs,
          },
        ],
        glossaryHits: [],
      },
    };
    const turn = {
      schema: "voxagent.conversation.turn",
      schemaVersion: 1,
      type: "conversation.turn",
      eventId: "conversation-turn-1",
      sessionId: session.id,
      sequence: 1,
      participantRole: "HOST",
      clientEventId: "mobile-turn-1",
      speechEvent,
      translationEvent,
      createdAt: "2026-07-19T12:00:03Z",
    };
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(turn), { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await publishConversationTurn(session.id, participantToken, {
      clientEventId: "mobile-turn-1",
      speechEvent,
      translationEvent,
    });

    expect(result.ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(
      "http://127.0.0.1:18900/public/v1/conversation-sessions/conv_123/turns"
    );
    expect(url).not.toContain(participantToken);
    const headers = new Headers(init.headers);
    expect(headers.get("X-VoxAgent-Conversation-Token")).toBe(participantToken);
    expect(headers.get("Authorization")).toBeNull();
    const body = JSON.parse(init.body as string);
    expect(body.speechEvent.type).toBe("transcript.final");
    expect(body.translationEvent.payload.sourceSegments).toEqual([
      {
        streamId: speechEvent.streamId,
        segmentId: speechEvent.payload.segmentId,
        revision: speechEvent.payload.revision,
        audioStartMs: speechEvent.payload.audioStartMs,
        audioEndMs: speechEvent.payload.audioEndMs,
      },
    ]);
  });
});
