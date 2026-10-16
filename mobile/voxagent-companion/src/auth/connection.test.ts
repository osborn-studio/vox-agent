import { describe, expect, it, vi } from "vitest";

import { probeEngineConnection } from "./connection";

const TOKEN = "member-token-with-enough-entropy";
const ORIGIN = "https://engine.example.test";

describe("engine connection probe", () => {
  it("checks public health before verifying the principal credential", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "ok" }), { status: 200 })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify([]), { status: 200 })
      );

    const result = await probeEngineConnection(TOKEN, ORIGIN, fetchMock);

    expect(result).toEqual({ ok: true });
    expect(fetchMock.mock.calls[0][0]).toBe(`${ORIGIN}/health`);
    expect(fetchMock.mock.calls[1][0]).toBe(
      `${ORIGIN}/v1/capture/sessions?limit=1`
    );
    const authHeaders = new Headers(fetchMock.mock.calls[1][1]?.headers);
    expect(authHeaders.get("Authorization")).toBe(`Bearer ${TOKEN}`);
    expect(JSON.stringify(result)).not.toContain(TOKEN);
    expect(fetchMock.mock.calls.map(([url]) => url).join(" ")).not.toContain(
      TOKEN
    );
  });

  it.each([
    [401, "AUTH_REQUIRED"],
    [403, "AUTHORIZATION_DENIED"],
    [503, "HEALTH_REJECTED"],
  ] as const)("classifies protected status %i as %s", async (status, code) => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "ok" }), { status: 200 })
      )
      .mockResolvedValueOnce(new Response("{}", { status }));

    expect(await probeEngineConnection(TOKEN, ORIGIN, fetchMock)).toEqual({
      ok: false,
      code,
    });
  });

  it("rejects an unhealthy public response before sending the credential", async () => {
    const fetchMock = vi.fn(async () =>
      Promise.resolve(
        new Response(JSON.stringify({ status: "starting" }), { status: 200 })
      )
    );

    expect(await probeEngineConnection(TOKEN, ORIGIN, fetchMock)).toEqual({
      ok: false,
      code: "HEALTH_REJECTED",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["certificate verify failed", "TLS_ERROR"],
    ["getaddrinfo ENOTFOUND", "DNS_ERROR"],
    ["Network request failed", "NETWORK_UNREACHABLE"],
  ] as const)("classifies transport error %s as %s", async (message, code) => {
    const fetchMock = vi.fn(async () => {
      throw new Error(message);
    });

    expect(await probeEngineConnection(TOKEN, ORIGIN, fetchMock)).toEqual({
      ok: false,
      code,
    });
  });
});
