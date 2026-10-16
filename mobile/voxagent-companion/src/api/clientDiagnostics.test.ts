import { afterEach, describe, expect, it, vi } from "vitest";

import { searchMemory } from "./client";
import {
  clearRequestDiagnosticsForTests,
  getRequestDiagnostics,
} from "../diagnostics/runtime";

afterEach(() => {
  clearRequestDiagnosticsForTests();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("API diagnostic metadata", () => {
  it("sends a correlation ID and records only response metadata", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ detail: "private memory body" }), {
        status: 503,
        headers: { "X-Request-ID": "engine-request_456" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(searchMemory("private transcript query")).resolves.toMatchObject(
      {
        ok: false,
        code: "REQUEST_REJECTED",
      }
    );

    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(new Headers(init.headers).get("X-Request-ID")).toMatch(
      /^[A-Za-z0-9_-]{8,64}$/
    );
    const diagnostics = getRequestDiagnostics();
    expect(diagnostics).toEqual([
      expect.objectContaining({
        correlationId: "engine-request_456",
        endpointClass: "loopback",
        networkState: "reachable",
        errorCode: "REQUEST_REJECTED",
      }),
    ]);
    expect(JSON.stringify(diagnostics)).not.toContain("private transcript");
    expect(JSON.stringify(diagnostics)).not.toContain("private memory");
  });

  it("keeps a local correlation ID when the engine is offline", async () => {
    const fetchMock = vi.fn(async () => {
      throw new Error("private network detail");
    });
    vi.stubGlobal("fetch", fetchMock);

    await searchMemory("private prompt");

    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    const correlationId = new Headers(init.headers).get("X-Request-ID");
    expect(getRequestDiagnostics()).toEqual([
      expect.objectContaining({
        correlationId,
        networkState: "offline",
        errorCode: "ENGINE_OFFLINE",
      }),
    ]);
    expect(JSON.stringify(getRequestDiagnostics())).not.toContain(
      "private network detail"
    );
  });
});
