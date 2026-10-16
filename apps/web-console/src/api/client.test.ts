import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createClient } from "./client";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("web console api client", () => {
  it("returns parsed sessions on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify([
              {
                id: "cap_1",
                workspaceId: "ws",
                ownerUserId: "u",
                edition: "PERSONAL",
                source: "MOBILE",
                mode: "MEETING",
                title: "T",
                startedAt: "t",
                endedAt: null,
                languageHints: [],
                detectedLanguages: [],
                privacyLevel: "PERSONAL",
                consentState: "GRANTED",
                deviceIds: [],
                localOnlyUntilSync: false,
                status: "READY",
                createdAt: "t",
                updatedAt: "t"
              }
            ]),
            { status: 200 }
          )
      )
    );
    const client = createClient("http://127.0.0.1:18900");
    const sessions = await client.listSessions();
    expect(sessions[0].id).toBe("cap_1");
  });

  it("returns parsed audit events on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify([
              {
                id: "aud_1",
                action: "agent.query",
                actorId: null,
                workspaceId: "ws",
                targetId: null,
                metadata: { query: "x" },
                createdAt: "t"
              }
            ]),
            { status: 200 }
          )
      )
    );
    const client = createClient("http://127.0.0.1:18900");
    const events = await client.getAuditEvents();
    expect(events[0].action).toBe("agent.query");
  });

  it("getAuditEvents fails closed on network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("refused");
    }));
    const client = createClient("http://127.0.0.1:18900");
    await expect(client.getAuditEvents()).rejects.toBeInstanceOf(ApiError);
  });

  it("falls back to mock data on network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("refused");
      })
    );
    const client = createClient("http://127.0.0.1:18900");
    const result = await client.searchMemory("anything");
    expect(Array.isArray(result.results)).toBe(true); // mock shape, no throw
  });

  it("aborts a hung request and falls back", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          })
      )
    );
    const client = createClient("http://127.0.0.1:18900", 20); // 20ms timeout
    const devices = await client.listDevices();
    expect(Array.isArray(devices)).toBe(true); // fell back, didn't hang
  });

  it("askAgent returns a live result with parsed data on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              query: "japan launch",
              answer: "Based on 1 memory item...",
              answerer: "mock",
              citations: [{ id: "mem_1", title: "Launch in Japan", type: "DECISION" }],
              contextNodeIds: ["mem_1"]
            }),
            { status: 200 }
          )
      )
    );
    const client = createClient("http://127.0.0.1:18900");
    const result = await client.askAgent("japan launch");
    expect(result.ok).toBe(true);
    expect(result.offline).toBe(false);
    expect(result.data.answerer).toBe("mock");
    expect(result.data.citations[0].id).toBe("mem_1");
  });

  it("askAgent returns an offline result carrying the fallback answer on network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("refused");
      })
    );
    const client = createClient("http://127.0.0.1:18900");
    const result = await client.askAgent("anything");
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected offline result");
    expect(result.offline).toBe(true);
    expect(result.status).toBeUndefined();
    // fallback answer is still carried so the UI can render it beneath an offline banner
    expect(result.data.answerer).toBe("mock");
    expect(result.data.answer).toContain("offline");
  });

  it("createPairingCode returns a live result on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              code: "AB12CD",
              qrPayload: "voxagent://pair/AB12CD",
              expiresAt: "2026-07-05T09:10:00Z"
            }),
            { status: 200 }
          )
      )
    );
    const client = createClient("http://127.0.0.1:18900");
    const result = await client.createPairingCode();
    expect(result.ok).toBe(true);
    expect(result.data.code).toBe("AB12CD");
  });

  it("pairDevice reports a server error (non-2xx) instead of a fake mock success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: "unknown code" }), { status: 404 }))
    );
    const client = createClient("http://127.0.0.1:18900");
    const result = await client.pairDevice("BADCODE", "user_local", "ws_demo");
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected error result");
    expect(result.offline).toBe(false);
    expect(result.status).toBe(404);
  });

  it("pairDevice reports offline (no status) on network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("refused");
      })
    );
    const client = createClient("http://127.0.0.1:18900");
    const result = await client.pairDevice("ANYCODE", "user_local", "ws_demo");
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected offline result");
    expect(result.offline).toBe(true);
    expect(result.status).toBeUndefined();
  });

  it("returns the model policy on success", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({
        answerer: "llm", asrAdapter: "sherpa",
        answererOverride: "llm", asrAdapterOverride: null,
        availableAnswerers: ["llm", "mock"], availableAsrAdapters: ["mock", "sherpa"],
        llmConfigured: true, llmModel: "deepseek-chat"
      }), { status: 200 })
    ));
    const client = createClient("http://127.0.0.1:18900");
    const policy = await client.getModelPolicy();
    expect(policy.answerer).toBe("llm");
    expect(policy.llmConfigured).toBe(true);
  });

  it("getModelPolicy fails closed on network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("refused"); }));
    const client = createClient("http://127.0.0.1:18900");
    await expect(client.getModelPolicy()).rejects.toBeInstanceOf(ApiError);
  });

  it("returns the retention policy on success", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({
        audioRetentionDays: 14, transcriptRetentionDays: 90, memoryRetentionDays: 365,
        autoDeleteEnabled: true,
        audioRetentionDaysOverride: 14, transcriptRetentionDaysOverride: null,
        memoryRetentionDaysOverride: null, autoDeleteOverride: true
      }), { status: 200 })
    ));
    const client = createClient("http://127.0.0.1:18900");
    const retention = await client.getRetentionPolicy();
    expect(retention.audioRetentionDays).toBe(14);
    expect(retention.audioRetentionDaysOverride).toBe(14);
    expect(retention.autoDeleteEnabled).toBe(true);
  });

  it("getRetentionPolicy fails closed on network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("refused"); }));
    const client = createClient("http://127.0.0.1:18900");
    await expect(client.getRetentionPolicy()).rejects.toBeInstanceOf(ApiError);
  });

  it("previews retention deletion with an explicit dry-run request", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      id: "sweep_1",
      dryRun: true,
      startedAt: "2026-07-17T00:00:00Z",
      completedAt: "2026-07-17T00:00:01Z",
      cutoffs: { audio: "a", transcripts: "t", memory: "m" },
      candidates: { audio: 2, transcripts: 1, memory: 3 },
      deleted: { audio: 0, transcripts: 0, memory: 0 },
      failures: []
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const client = createClient("http://127.0.0.1:18900");

    const result = await client.runRetentionSweep(true);

    expect(result.candidates.audio).toBe(2);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ dryRun: true });
  });

  it("keeps the admin token in the client and sends it as a bearer credential", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify([]), { status: 200 })
    );
    const localStorageSet = vi.fn();
    vi.stubGlobal("localStorage", { setItem: localStorageSet });
    vi.stubGlobal("fetch", fetchMock);
    const client = createClient("http://127.0.0.1:18900");

    client.setAdminToken("session-only-secret");
    await client.getAuditEvents();

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer session-only-secret");
    expect(localStorageSet).not.toHaveBeenCalled();
  });

  it("parses the admin user list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([{
      id: "user_1",
      orgId: "org_1",
      email: "admin@example.com",
      displayName: "Administrator",
      role: "admin",
      status: "active",
      createdAt: "2026-07-17T00:00:00Z"
    }]), { status: 200 })));
    const client = createClient("http://127.0.0.1:18900");

    const users = await client.listAdminUsers();

    expect(users[0].role).toBe("admin");
  });
});
