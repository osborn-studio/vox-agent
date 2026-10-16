import { beforeEach, describe, expect, it, vi } from "vitest";

const { share } = vi.hoisted(() => ({
  share: vi.fn(
    async (_payload: { title: string; message: string }) => ({
      action: "sharedAction",
    })
  ),
}));

vi.mock("expo-constants", () => ({
  default: {
    executionEnvironment: "standalone",
    expoConfig: { version: "4.1.0", ios: { buildNumber: "410001" } },
    platform: { ios: { buildNumber: "410001" } },
  },
}));

vi.mock("react-native", () => ({
  Platform: {
    OS: "ios",
    Version: "18.5",
    isPad: false,
    isTV: false,
    constants: {},
  },
  Share: {
    share,
    sharedAction: "sharedAction",
  },
}));

import {
  clearRequestDiagnosticsForTests,
  recordRequestDiagnostic,
} from "./runtime";
import { sharePrivacySafeDiagnostics } from "./share";

describe("sharePrivacySafeDiagnostics", () => {
  beforeEach(() => {
    clearRequestDiagnosticsForTests();
    share.mockClear();
  });

  it("opens the native share sheet with a content-free scanned bundle", async () => {
    recordRequestDiagnostic({
      correlationId: "mobile-request_123",
      endpointOrigin: "https://engine.example.test/private/path",
      networkState: "reachable",
      error: {
        code: "REQUEST_REJECTED",
        message: "private transcript",
      },
    });

    await expect(sharePrivacySafeDiagnostics()).resolves.toBe(true);
    const payload = share.mock.calls[0][0];
    expect(payload.title).toBe("Vox Agent diagnostics");
    expect(payload.message).toContain("mobile-request_123");
    expect(payload.message).toContain("REQUEST_REJECTED");
    expect(payload.message).not.toContain("engine.example.test");
    expect(payload.message).not.toContain("private transcript");
  });
});
