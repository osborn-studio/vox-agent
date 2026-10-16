import { afterEach, describe, expect, it, vi } from "vitest";

import {
  classifyDiagnosticEndpoint,
  clearRequestDiagnosticsForTests,
  createRequestCorrelationId,
  getRequestDiagnostics,
  installRequestCorrelation,
  recordRequestDiagnostic,
  redactDiagnosticErrorCode,
  responseCorrelationId,
} from "./runtime";

afterEach(() => {
  clearRequestDiagnosticsForTests();
  vi.restoreAllMocks();
});

describe("diagnostic request metadata", () => {
  it.each([
    ["http://127.0.0.1:18900", "loopback"],
    ["http://192.168.1.20:18900", "private-lan"],
    ["https://engine.example.test", "remote-https"],
    ["http://engine.example.test", "remote-http"],
    ["file:///tmp/private-session", "invalid"],
  ] as const)("classifies an endpoint without retaining its host", (origin, expected) => {
    expect(classifyDiagnosticEndpoint(origin)).toBe(expected);
  });

  it("creates and echoes only bounded correlation IDs", () => {
    vi.spyOn(Date, "now").mockReturnValue(1_785_104_800_000);
    const headers = new Headers({ "X-Request-ID": "unsafe private value" });
    const created = installRequestCorrelation(headers);

    expect(created).toBe(createRequestCorrelationId().replace(/-2$/, "-1"));
    expect(created).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(headers.get("x-request-id")).toBe(created);
    expect(
      responseCorrelationId(
        new Response(null, {
          headers: { "X-Request-ID": "engine-request_456" },
        }),
        created
      )
    ).toBe("engine-request_456");
    expect(
      responseCorrelationId(
        new Response(null, {
          headers: { "X-Request-ID": "unsafe private value" },
        }),
        created
      )
    ).toBe(created);
  });

  it("reduces representative private error payloads to a bounded code", () => {
    const rawError = {
      code: "ENGINE_OFFLINE",
      message: "private transcript",
      translation: "private translation",
      memoryBody: "private memory",
      prompt: "private prompt",
      credential: "private credential",
      qrPayload: "private QR payload",
      contact: "private contact",
      path: "/private/session/path",
    };
    expect(redactDiagnosticErrorCode(rawError)).toBe("ENGINE_OFFLINE");
    expect(
      redactDiagnosticErrorCode({
        ...rawError,
        code: "unsafe code with private content",
      })
    ).toBe("UNCLASSIFIED_ERROR");

    recordRequestDiagnostic({
      correlationId: "mobile-request_123",
      endpointOrigin: "https://user:private@example.test/private",
      networkState: "offline",
      error: rawError,
      now: new Date("2026-07-26T12:00:00Z"),
    });
    const serialized = JSON.stringify(getRequestDiagnostics());
    expect(serialized).toContain("ENGINE_OFFLINE");
    for (const privateValue of Object.values(rawError).slice(1)) {
      expect(serialized).not.toContain(privateValue);
    }
  });

  it("keeps a bounded, copy-safe in-memory request history", () => {
    for (let index = 0; index < 25; index += 1) {
      recordRequestDiagnostic({
        correlationId: `mobile-request_${index}`,
        endpointOrigin: "https://engine.example.test",
        networkState: "reachable",
        error: { code: index === 24 ? "AUTH_REQUIRED" : "OK" },
        now: new Date(`2026-07-26T12:00:${String(index).padStart(2, "0")}Z`),
      });
    }
    const firstRead = getRequestDiagnostics();
    expect(firstRead).toHaveLength(20);
    expect(firstRead[0].errorCode).toBe("AUTH_REQUIRED");
    firstRead[0].errorCode = "MUTATED";
    expect(getRequestDiagnostics()[0].errorCode).toBe("AUTH_REQUIRED");
  });
});
